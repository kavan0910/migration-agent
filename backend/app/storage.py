"""ObjectStore interface (contract section 5). Keys are always prefixed ``{workspace_id}/{project_id}/``."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Protocol

from .config import Settings


class StoreError(Exception):
    pass


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def put_file(self, key: str, path: Path) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def list(self, prefix: str) -> list[str]: ...
    def list_project_prefixes(self) -> list[str]: ...  # every "{workspace}/{project}/" that holds objects


def project_prefix(workspace_id: str, project_id: str) -> str:
    return f"{workspace_id}/{project_id}/"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_key(key: str) -> None:
    parts = key.split("/")
    if (not key or key.startswith("/") or "\\" in key or "\x00" in key or any(p in ("", ".", "..") for p in parts[:-1])
            or parts[-1] in (".", "..")):
        raise StoreError("invalid object key")


class LocalStore:
    """Filesystem store for dev and tests. Writes are atomic (temp file + rename)."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _check_key(key)
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise StoreError("key escapes store root")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def put_file(self, key: str, path: Path) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        os.close(fd)
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key: str) -> bytes:
        p = self._path(key)
        try:
            return p.read_bytes()
        except FileNotFoundError:
            raise StoreError("not found") from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            raise StoreError("prefix must end with '/'")
        _check_key(prefix.rstrip("/") + "/x")
        d = self._path(prefix.rstrip("/") + "/x").parent
        n = sum(1 for p in d.rglob("*") if p.is_file()) if d.exists() else 0
        if d.exists():
            shutil.rmtree(d, ignore_errors=False)
        return n

    def list_project_prefixes(self) -> list[str]:
        out = []
        for w in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for pr in sorted(p for p in w.iterdir() if p.is_dir()):
                out.append(f"{w.name}/{pr.name}/")
        return out

    def list(self, prefix: str) -> list[str]:
        base = self.root
        out = []
        start = self._path(prefix.rstrip("/") + "/x").parent if prefix.endswith("/") else self.root
        if not start.exists():
            return []
        for p in start.rglob("*"):
            if p.is_file() and not p.name.startswith(".tmp-"):
                k = p.relative_to(base).as_posix()
                if k.startswith(prefix):
                    out.append(k)
        return sorted(out)


class S3Store:
    """Skeleton behind the same interface. Needs ``boto3`` (extra ``s3``); not exercised by the test-suite.

    Server-side encryption (SSE-S3/KMS) and lifecycle rules are bucket configuration (see deploy docs); this class
    requests SSE by default.
    """

    def __init__(self, bucket: str, endpoint_url: str | None = None, region: str | None = None, client=None):
        if client is None:
            try:
                import boto3  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise StoreError("boto3 is required for the S3 store (pip install '.[s3]')") from e
            client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
        self.bucket, self.c = bucket, client

    def put(self, key: str, data: bytes) -> None:
        _check_key(key)
        self.c.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="AES256")

    def put_file(self, key: str, path: Path) -> None:
        _check_key(key)
        self.c.upload_file(str(path), self.bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})

    def get(self, key: str) -> bytes:
        _check_key(key)
        try:
            return self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as e:  # botocore ClientError NoSuchKey
            raise StoreError("not found") from e

    def exists(self, key: str) -> bool:
        _check_key(key)
        try:
            self.c.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        _check_key(key)
        self.c.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.c.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return keys
            token = r["NextContinuationToken"]

    def list_project_prefixes(self) -> list[str]:
        out: list[str] = []
        for wp in self._common_prefixes(""):
            out += self._common_prefixes(wp)
        return out

    def _common_prefixes(self, prefix: str) -> list[str]:
        res: list[str] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "Delimiter": "/"}
            if token:
                kw["ContinuationToken"] = token
            r = self.c.list_objects_v2(**kw)
            res += [p["Prefix"] for p in r.get("CommonPrefixes", [])]
            if not r.get("IsTruncated"):
                return res
            token = r["NextContinuationToken"]

    def _versions(self, prefix: str) -> list[dict]:
        """Every object version and delete marker under prefix (a versioned bucket keeps data after a plain delete)."""
        out: list[dict] = []
        kw: dict = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            r = self.c.list_object_versions(**kw)
            for v in list(r.get("Versions", [])) + list(r.get("DeleteMarkers", [])):
                o = {"Key": v["Key"]}
                if v.get("VersionId"):
                    o["VersionId"] = v["VersionId"]
                out.append(o)
            if not r.get("IsTruncated"):
                return out
            kw["KeyMarker"] = r.get("NextKeyMarker")
            if r.get("NextVersionIdMarker"):
                kw["VersionIdMarker"] = r["NextVersionIdMarker"]

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object AND every version / delete marker. Raises StoreError unless the store confirmed every
        deletion (``delete_objects`` reports per-key failures in ``Errors`` instead of raising)."""
        if not prefix.endswith("/"):
            raise StoreError("prefix must end with '/'")
        objs = self._versions(prefix)
        known = {o["Key"] for o in objs}
        objs += [{"Key": k} for k in self.list(prefix) if k not in known]
        failed: list[str] = []
        for i in range(0, len(objs), 1000):
            r = self.c.delete_objects(Bucket=self.bucket, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
            failed += [f"{e.get('Key')}:{e.get('Code')}" for e in (r or {}).get("Errors", [])]
        if failed:
            raise StoreError(f"{len(failed)} object(s) could not be deleted (first: {failed[0]})")
        if self.list(prefix) or self._versions(prefix):  # verify, do not assume
            raise StoreError("objects still present after delete")
        return len({o["Key"] for o in objs})


def make_store(s: Settings) -> ObjectStore:
    if s.store_backend == "s3":
        if not s.s3_bucket:
            raise StoreError("S3_BUCKET is required for STORE_BACKEND=s3")
        return S3Store(s.s3_bucket, s.s3_endpoint_url, s.s3_region)
    return LocalStore(s.store_dir)


__all__: Iterable[str] = ["ObjectStore", "LocalStore", "S3Store", "make_store", "project_prefix", "sha256_hex", "StoreError"]

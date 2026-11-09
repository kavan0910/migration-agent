# syntax=docker/dockerfile:1
# Production image for migration-agent (D-03), built on the A-09 sandbox baseline:
#   multi-stage (wheel build -> slim runtime), non-root uid 10001, read-only rootfs compatible,
#   no credentials baked in (keys are injected at run time via env / secrets).
#
# Build (context = repo root):
#   docker build -f docker/Dockerfile \
#     --build-arg VERSION=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])") \
#     --build-arg VCS_REF=$(git rev-parse HEAD) --build-arg BUILD_DATE=$(date -u +%FT%TZ) \
#     -t migration-agent:0.2.0 .
#
# PINNING THE BASE IMAGE BY DIGEST (do this for every release build):
#   docker buildx imagetools inspect python:3.12-slim      # copy the top-level "Digest: sha256:..."
#   docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> ...
# The default below is a moving tag so local builds work; release builds must pass the digest
# (CI does so via the PYTHON_IMAGE repository variable, and scripts/release.py reminds you).
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
# Only what the wheel needs (see .dockerignore): keeps the layer cache stable and the context small.
COPY pyproject.toml README.md ./
COPY src ./src
# Wheel for the project, plus wheels for every dependency incl. the [llm] extra, so the runtime
# stage installs offline from /wheels and never needs compilers or network.
RUN pip install --no-cache-dir build \
 && python -m build --wheel --outdir /wheels . \
 && pip wheel --no-cache-dir --wheel-dir /wheels "$(ls /wheels/migration_agent-*.whl)[llm]"

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.0.0-dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="migration-agent" \
      org.opencontainers.image.description="Read-only legacy service analysis: evidence-backed understanding docs and knowledge graph" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/OWNER/migration-agent" \
      org.opencontainers.image.licenses="UNLICENSED" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}"

# HOME and caches point at tmpfs (/tmp) so a --read-only root filesystem works.
# LITELLM_LOCAL_MODEL_COST_MAP avoids a network fetch of the price map at import time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    TIKTOKEN_CACHE_DIR=/tmp/tiktoken \
    LITELLM_LOCAL_MODEL_COST_MAP=True

RUN groupadd --gid 10001 agent \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin agent \
 && mkdir -p /workspace /project /scratch \
 && chown agent:agent /scratch /project

# Code is installed root-owned from the wheels: not writable by the agent user.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels "$(ls /wheels/migration_agent-*.whl)[llm]" \
 && rm -rf /root/.cache

# /workspace : the source repository under analysis, mount READ-ONLY.
# /project   : the <name>.migration project folder (the only durable state), mount read-write.
#              Created empty here so the mount point exists even on a read-only rootfs.
WORKDIR /project
USER 10001:10001

# No ENV for keys/tokens on purpose: pass ANTHROPIC_API_KEY / GEMINI_API_KEY / ... at run time.
# Configuration is via MA_* variables (see docs/deployment.md).
#
# No HEALTHCHECK: the CLI is run-to-completion, and `review` binds 127.0.0.1 inside the container
# (with a Host-header check), so it cannot be health-probed or reached through a published port.
# Run `migration-agent review` on the host against the project folder instead (docs/deployment.md).
ENTRYPOINT ["migration-agent"]
CMD ["--help"]

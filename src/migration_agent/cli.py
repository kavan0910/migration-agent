from __future__ import annotations

from pathlib import Path

import click

from .project import Project


@click.group()
def main() -> None:
    """Read-only legacy service analysis."""


@main.command()
@click.argument("source")
@click.option("--project", "project_dir", type=click.Path(path_type=Path), default=None,
              help="Project folder to create (default: ./<source-name>.migration)")
@click.option("--name", default=None)
def init(source: str, project_dir: Path | None, name: str | None) -> None:
    """Create a project folder for SOURCE (path, zip, tarball or git URL)."""
    default = Path(Path(source.rstrip("/")).stem or "service").with_suffix(".migration")
    p = Project.init(project_dir or default, source, name)
    click.echo(f"project created: {p.root}")


def _proj(path: Path) -> Project:
    return Project.load(path)


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
def ingest_cmd(project: Path) -> None:
    """Copy the project's source into code/ (path, zip, tarball or git URL)."""
    from .audit import AuditLog
    from .pipeline import run_ingest
    p = _proj(project)
    with AuditLog(p.audit_dir / "audit.jsonl").span("cli", "ingest"):
        r = run_ingest(p, p.meta().source)
    click.echo(f"ingested {r.files_copied} files, skipped {r.files_skipped}, secret findings {len(r.secret_findings)}")
    (p.root / "ingest_report.json").write_text(r.model_dump_json(indent=2))


main.add_command(ingest_cmd, "ingest")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
def extract(project: Path) -> None:
    """Run static extraction and write the coverage inventory."""
    from .audit import AuditLog
    from .pipeline import run_extract
    p = _proj(project)
    with AuditLog(p.audit_dir / "audit.jsonl").span("cli", "extract"):
        inv = run_extract(p)
    click.echo(f"inventory: {len(inv.items)} items {inv.counts}")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
def render(project: Path) -> None:
    """Build the facts-only graph and render the understanding doc."""
    from .audit import AuditLog
    from .pipeline import run_render
    p = _proj(project)
    with AuditLog(p.audit_dir / "audit.jsonl").span("cli", "render"):
        out = run_render(p)
    click.echo(f"wrote {out}")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def audit(project: Path, fmt: str) -> None:
    """Show the audit log with totals."""
    from .audit import render_audit
    click.echo(render_audit(_proj(project).audit_dir / "audit.jsonl", fmt=fmt))


if __name__ == "__main__":
    main()

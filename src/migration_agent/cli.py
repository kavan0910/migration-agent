from __future__ import annotations

from pathlib import Path

import click

from .project import Project


@click.group()
@click.version_option(package_name="migration-agent")
def main() -> None:
    """Read-only legacy service analysis."""
    from .config_env import configure_logging
    from .dotenv import load_dotenv, project_root
    load_dotenv(project_root() / ".env")
    configure_logging()


def _load_cfg(config_path):
    """LLM config: --config file wins, otherwise defaults overlaid with MA_* environment variables."""
    from .config_env import EnvConfigError, load_llm_config_from_env
    from .llm.litellm_client import LLMConfig
    try:
        return LLMConfig.load(config_path) if config_path else load_llm_config_from_env()
    except EnvConfigError as e:
        raise click.ClickException(str(e))


def _budget(max_usd):
    from .config_env import EnvConfigError, load_settings_from_env
    if max_usd is not None:
        return max_usd
    try:
        return load_settings_from_env().max_usd
    except EnvConfigError as e:
        raise click.ClickException(str(e))


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
@click.option("--refresh", is_flag=True, help="Replace the existing code/ copy with a fresh ingest of the source "
                                              "(use before `analyze --incremental` after the source changed).")
def ingest_cmd(project: Path, refresh: bool) -> None:
    """Copy the project's source into code/ (path, zip, tarball or git URL)."""
    from .pipeline import run_ingest_stage
    p = _proj(project)
    r = run_ingest_stage(p, refresh=refresh)
    click.echo(f"ingested {r.files_copied} files, skipped {r.files_skipped}, secret findings {len(r.secret_findings)}")


main.add_command(ingest_cmd, "ingest")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
def extract(project: Path) -> None:
    """Run static extraction and write the coverage inventory."""
    from .pipeline import run_extract_stage
    inv = run_extract_stage(_proj(project))
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


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
@click.option("--offline", is_flag=True, help="Run the pipeline with a deterministic no-model stand-in (no real analysis).")
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None, help="LLM config.json")
@click.option("--max-usd", type=float, default=None, help="Hard budget; the run stops gracefully when reached.")
@click.option("--dry-run", is_flag=True, help="Only print the chunk plan and cost estimate.")
@click.option("--incremental", is_flag=True,
              help="After re-ingesting updated source: re-analyse only changed chunks and their dependents (file-hash invalidation); IDs stay stable.")
@click.option("--workers", type=click.IntRange(min=1), default=1, show_default=True,
              help="Parallel chunk calls (dependency-aware; merge order stays deterministic).")
def analyze(project: Path, offline: bool, config_path: Path | None, max_usd: float | None, dry_run: bool,
            incremental: bool, workers: int) -> None:
    """Analyze the service: extract -> LLM analysis -> merge -> verify -> render doc and graph."""
    from .analyze.runner import estimate, run_analyze
    from .audit import AuditLog
    from .llm.litellm_client import Budget, LiteLLMClient, LLMConfig
    from .llm.offline import OfflineLLM
    from .pipeline import ensure_extracted
    import os
    if not offline and not dry_run and not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY")):
        raise click.ClickException("No LLM API key found. Add `ANTHROPIC_API_KEY=...` (or GEMINI_API_KEY) to the .env file in the migration-agent project root (see .env.example), or use --offline.")
    p = _proj(project)
    ensure_extracted(p, force=incremental)  # incremental: facts must reflect the re-ingested source (no LLM involved)
    cfg = _load_cfg(config_path)
    max_usd = _budget(max_usd)
    n, usd = estimate(p, cfg, incremental=incremental)
    click.echo(f"plan: {n} chunk(s), estimated cost ${usd:.2f}" + (" (offline: no cost)" if offline else "")
               + (" [incremental: pending chunks only]" if incremental else ""))
    if max_usd is not None and usd > max_usd and not offline:
        click.echo(f"warning: estimate ${usd:.2f} exceeds --max-usd ${max_usd:.2f}; the run will stop gracefully with partial results")
    if dry_run:
        return
    audit_log = AuditLog(p.audit_dir / "audit.jsonl")
    llm = OfflineLLM() if offline else LiteLLMClient(cfg, budget=Budget(max_usd=max_usd), cache_dir=p.root / "llm_cache", audit=audit_log)
    out = run_analyze(p, llm, log=click.echo, incremental=incremental, workers=workers, max_usd=max_usd)
    click.echo(f"done: {out}")
    if out.get("failed_chunks") or out.get("failed_tasks"):
        click.echo(f"warning: {out['failed_chunks']} chunk(s) failed ({', '.join(out['failed'] + out['failed_tasks'])}); "
                   f"partial results were rendered. Re-run `analyze` to retry only the failed/unfinished chunks "
                   f"(details: checkpoint/failures.json).")
    click.echo(f"cost: this session ${out['session_cost_usd']:.4f}, project total ${out['total_cost_usd']:.4f} (audit/cost_total.json)")
    click.echo(f"doc:   {p.docs / 'understanding.latest.md'}\ngraph: {p.docs / 'graph.html'}")


def _llm(p, config_path, max_usd):
    import os
    from .audit import AuditLog
    from .llm.litellm_client import Budget, LiteLLMClient, LLMConfig
    if not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY")):
        raise click.ClickException("No LLM API key found. Add it to the .env file in the migration-agent project root (see .env.example).")
    cfg = _load_cfg(config_path)
    return LiteLLMClient(cfg, budget=Budget(max_usd=_budget(max_usd)), cache_dir=p.root / "llm_cache",
                         audit=AuditLog(p.audit_dir / "audit.jsonl"))


_llm_opts = [
    click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None, help="LLM config.json"),
    click.option("--max-usd", type=float, default=None, help="Hard budget; stops gracefully when reached."),
]


def _with_llm_opts(f):
    for o in reversed(_llm_opts):
        f = o(f)
    return f


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
@_with_llm_opts
def verify(project: Path, config_path: Path | None, max_usd: float | None) -> None:
    """Run the blind Verifier: compare the understanding doc with user_docs/ and write feedback/findings.json."""
    from .verify import run_verifier
    p = _proj(project)
    if not (p.docs / "understanding.latest.md").exists():
        raise click.ClickException("No understanding doc yet. Run `analyze` first.")
    out = run_verifier(p, _llm(p, config_path, max_usd))
    by_kind: dict[str, int] = {}
    for f in out.findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    click.echo(f"findings: {len(out.findings)} {by_kind}; dropped unverifiable: {out.dropped_unverifiable}")
    for f in out.findings:
        click.echo(f"  {f.id} [{f.severity}] {f.kind}: {f.title}")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
@click.option("--port", type=int, default=0, help="Port (0 = random free port).")
@click.option("--no-browser", is_flag=True, help="Do not open a browser; just print the URL.")
def review(project: Path, port: int, no_browser: bool) -> None:
    """Start the local review server (127.0.0.1, token-protected) for comments and resolutions."""
    from .review_ui.server import serve
    server, url = serve(_proj(project).root, port=port, open_browser=not no_browser)
    click.echo(f"review server: {url}\nCtrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
@click.argument("finding_id", required=False)
@click.option("--status", type=click.Choice(["accepted_doc", "accepted_code", "dismissed", "needs_reanalysis"]), default=None)
@click.option("--note", default="")
@click.option("--comment", "comment_item", default=None, help="Attach a comment to this item ID instead (text via --note).")
@click.option("--reanalyze/--no-reanalyze", "do_re", default=True, help="Re-analyze affected code after recording.")
@_with_llm_opts
def resolve(project: Path, finding_id, status, note, comment_item, do_re, config_path, max_usd) -> None:
    """Record a resolution (or comment), then run targeted re-analysis and refresh docs/changelog."""
    from .review.changelog import generate_changelog
    from .review.comments import add_comment
    from .review.reanalyze import reanalyze
    from .review.resolutions import add_resolution
    from .review._io import ReviewError
    p = _proj(project)
    try:
        if comment_item:
            c = add_comment(p, comment_item, note)
            click.echo(f"comment {c.id} on {c.item_id}")
        elif finding_id and status:
            r = add_resolution(p, finding_id, status, note)
            click.echo(f"resolution for {r.finding_id}: {r.status} (affects {', '.join(r.affected_node_ids) or 'nothing'})")
        elif finding_id or status:
            raise click.UsageError("Give both FINDING_ID and --status, or use --comment ITEM_ID --note TEXT.")
    except ReviewError as e:
        raise click.ClickException(str(e))
    if not do_re:
        return
    from .review.resolutions import pending_reanalysis
    from .review.comments import unprocessed
    if not (pending_reanalysis(p) or unprocessed(p)):
        click.echo("nothing to re-analyze")
        return
    res = reanalyze(p, _llm(p, config_path, max_usd))
    if res.stopped:
        raise click.ClickException(f"stopped, nothing written: {res.stopped}")
    click.echo(f"re-analyzed {res.nodes_reanalyzed} node(s): graph v{res.old_version} -> v{res.new_version}; "
               f"+{len(res.added_nodes)} ~{len(res.changed_nodes)} -{len(res.removed_nodes)}")
    click.echo(f"changelog: {generate_changelog(p)}")


@main.command()
@click.argument("project", type=click.Path(exists=True, path_type=Path))
def changelog(project: Path) -> None:
    """Regenerate docs/changelog.md from graph version diffs."""
    from .review.changelog import generate_changelog
    click.echo(str(generate_changelog(_proj(project))))


if __name__ == "__main__":
    main()

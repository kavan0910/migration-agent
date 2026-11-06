import os

from click.testing import CliRunner

from migration_agent.dotenv import load_dotenv, parse_env_line


def test_parse():
    assert parse_env_line("A=1") == ("A", "1")
    assert parse_env_line("export B='x y'") == ("B", "x y")
    assert parse_env_line('C="q"') == ("C", "q")
    assert parse_env_line("D=v # c") == ("D", "v")
    assert parse_env_line("# c") is None and parse_env_line("junk") is None


def test_load_no_override(tmp_path, monkeypatch):
    monkeypatch.delenv("MA_T1", raising=False)
    monkeypatch.setenv("MA_T2", "keep")
    f = tmp_path / ".env"
    f.write_text("MA_T1=new\nMA_T2=clobber\n")
    assert load_dotenv(f) == ["MA_T1"]
    assert os.environ["MA_T1"] == "new" and os.environ["MA_T2"] == "keep"
    monkeypatch.delenv("MA_T1")


def test_missing_file(tmp_path):
    assert load_dotenv(tmp_path / "nope") == []


def test_analyze_requires_key(tmp_path, monkeypatch):
    from migration_agent.cli import main
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("migration_agent.dotenv.load_dotenv", lambda p: [])
    r = CliRunner().invoke(main, ["analyze", str(tmp_path)])
    assert r.exit_code != 0 and ".env" in r.output

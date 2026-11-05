import json
from pathlib import Path

from migration_agent.extract.config import REDACTED, ConfigFacts, extract_config, write_config

FIX = Path(__file__).parent / "fixtures" / "config"


def keys(facts):
    return {(k.file, k.key): k for k in facts.keys}


def test_deterministic_and_sorted():
    f = extract_config(FIX)
    assert f.files == sorted(f.files)
    assert f.model_dump_json() == extract_config(FIX).model_dump_json()


def test_env_example():
    k = keys(extract_config(FIX))
    assert k[(".env.example", "PORT")].value_preview == "8080"
    assert k[(".env.example", "PORT")].line == 2
    assert not k[(".env.example", "PORT")].is_secret
    assert k[(".env.example", "LOG_LEVEL")].value_preview == "debug"
    assert k[(".env.example", "DB_PASSWORD")].is_secret
    assert k[(".env.example", "DB_PASSWORD")].value_preview == REDACTED
    assert k[(".env.example", "EMPTY_TOKEN")].value_preview is None
    assert k[(".env.example", "DATABASE_URL")].is_secret  # embedded creds


def test_properties_yaml():
    k = keys(extract_config(FIX))
    assert k[("spring/application.properties", "server.port")].value_preview == "8080"
    assert k[("spring/application.properties", "spring.datasource.password")].is_secret
    assert not k[("spring/application.properties", "spring.datasource.url")].is_secret
    y = k[("spring/application.yml", "spring.datasource.password")]
    assert y.is_secret and y.line == 4
    assert k[("spring/application.yml", "spring.datasource.url")].line == 3


def test_docker_compose_env_forms():
    k = keys(extract_config(FIX))
    assert k[("docker-compose.yml", "services.web.environment.SECRET_KEY")].is_secret
    assert k[("docker-compose.yml", "services.web.environment.MODE")].value_preview == "prod"
    assert k[("docker-compose.yml", "services.db.environment.POSTGRES_PASSWORD")].is_secret
    assert not k[("docker-compose.yml", "services.db.environment.POSTGRES_DB")].is_secret


def test_json_toml_ini():
    k = keys(extract_config(FIX))
    assert k[("dotnet/appsettings.json", "Jwt.Secret")].is_secret
    assert k[("dotnet/appsettings.json", "ConnectionStrings.Default")].is_secret
    assert k[("dotnet/appsettings.json", "Logging.Level")].value_preview == "Info"
    assert not k[("dotnet/appsettings.json", "Author")].is_secret
    assert k[("dotnet/appsettings.json", "Jwt.Secret")].line == 4
    assert k[("config.toml", "database.password")].is_secret
    assert k[("config.toml", "database.host")].value_preview == "localhost"
    assert k[("settings.ini", "main.token")].is_secret
    assert k[("settings.ini", "main.name")].value_preview == "demo"


def test_no_secret_value_leaks():
    blob = extract_config(FIX).model_dump_json()
    for leaked in ("hunter2", "abc123", "s3cret", "topsecret", "pgpass", "tomlpw", "tok123",
                   "shh", "user:pw", "zzz"):
        assert leaked not in blob, leaked


def test_malformed_recorded():
    f = extract_config(FIX)
    assert "bad/broken.yml" in f.errors
    assert "bad/appsettings.json" in f.errors
    assert "spring/application.yml" in f.files


def test_long_value_truncated(tmp_path):
    (tmp_path / ".env.sample").write_text("NOTE=" + "x" * 200 + "\n")
    k = extract_config(tmp_path).keys[0]
    assert len(k.value_preview) < 80 and not k.is_secret


def test_write_roundtrip(tmp_path):
    f = extract_config(FIX)
    out = write_config(f, tmp_path / "facts")
    assert out.name == "config.json"
    assert ConfigFacts.model_validate(json.loads(out.read_text())) == f

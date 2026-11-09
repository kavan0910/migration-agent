# Red-team fixtures (D-06)

Fixtures are generated at test time by `scripts/make_redteam.py` into pytest tmp dirs; only
`payloads.json` (inert injection strings) is committed. No zip bombs and no live-looking credentials
are stored in the repository: fake secrets contain `EXAMPLE` and are assembled at runtime.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/redteam`
Manual build: `python scripts/make_redteam.py /tmp/redteam_out`

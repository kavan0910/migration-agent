"""E2E fixtures (helpers live in e2e_support.py)."""
import pytest

from e2e_support import REPOS, Run, get_run, install_replay


@pytest.fixture(scope="session")
def mp():
    with pytest.MonkeyPatch.context() as m:
        install_replay(m)
        yield m


@pytest.fixture(scope="session")
def base_tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("e2e")


@pytest.fixture(scope="session", params=REPOS)
def run(request, mp, base_tmp) -> Run:
    return get_run(request.param, base_tmp, mp)


@pytest.fixture(scope="session")
def express(mp, base_tmp) -> Run:
    return get_run("express-crud", base_tmp, mp)

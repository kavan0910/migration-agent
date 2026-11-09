from pathlib import Path

import migration_agent.extract.specs as specs
from migration_agent.extract.specs import extract_specs, route_identity

FX = Path(__file__).parent / "fixtures" / "frameworks"


def run(rel):
    return extract_specs(rel, (FX / rel).read_text())


def test_openapi_json_operation_id_suffix():
    f, w = run("openapi/openapi.json")
    assert w == []
    assert [(x.name, x.line) for x in f] == [("GET /pets (listPets)", 6), ("POST /pets", 7), ("DELETE /pets/{id} (deletePet)", 10)]
    assert [route_identity(x.name) for x in f] == ["GET /pets", "POST /pets", "DELETE /pets/{id}"]


def test_swagger2_yaml_basepath():
    f, w = run("openapi/swagger.yaml")
    assert [x.name for x in f] == ["POST /v2/store/order (placeOrder)", "GET /v2/store/inventory"]
    assert [x.line for x in f] == [5, 8]


def test_proto_services_and_messages():
    f, _ = run("proto/shop.proto")
    assert [x.name for x in f if x.kind == "route"] == ["RPC Catalog/GetItem", "RPC Catalog/Watch"]
    ents = {x.name: [(c["name"], c["type"]) for c in x.attrs["columns"]] for x in f if x.kind == "schema_entity"}
    assert ents["Item"] == [("id", "string"), ("tags", "string"), ("attrs", "map<string,int32>"), ("note", "string")]
    assert ents["Item.Price"] == [("cents", "int64")] and ents["GetItemRequest"] == [("id", "string")]


def test_malformed_and_unrelated_never_raise():
    assert extract_specs("a.json", '{"openapi": "3", ') [0] == []
    assert extract_specs("a.json", '{"openapi": "3", ')[1]  # warning
    assert extract_specs("package.json", '{"name": "x"}') == ([], [])
    assert extract_specs("a.yaml", "openapi: [unclosed")[1]
    assert extract_specs("a.yaml", "openapi: 3\npaths: 5") == ([], [])
    assert extract_specs("a.proto", "service { rpc ((( }}}}")[0] == []
    assert extract_specs("a.json", '{"openapi":"3","paths":{"/x":{"get":{"operationId":"a b"}},"/y":[1]}}')[0][0].name == "GET /x"


def test_size_cap(monkeypatch):
    monkeypatch.setattr(specs, "MAX_SPEC_BYTES", 10)
    f, w = extract_specs("a.proto", "message A { string x = 1; }")
    assert f == [] and "skipped" in w[0]


def test_yaml_missing_warns(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "yaml":
            raise ImportError
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    f, w = extract_specs("a.yaml", "openapi: 3.0.0\npaths: {}\n")
    assert f == [] and "PyYAML" in w[0]

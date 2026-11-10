from pathlib import Path

from migration_agent.extract.frameworks import extract_routes

FX = Path(__file__).parent / "fixtures" / "frameworks"


def routes(rel):
    return [(f.name, f.line) for f in extract_routes(rel, (FX / rel).read_text())]


def names(rel):
    return [n for n, _ in routes(rel)]


def test_spring_class_prefix_and_methods():
    r = routes("spring/OrderController.java")
    assert r == [
        ("GET /api/orders", 9), ("GET /api/orders/{id}", 12),
        ("POST /api/orders", 15), ("POST /api/orders/bulk", 15),
        ("PUT /api/orders/{id}/cancel", 18), ("POST /api/orders/{id}/cancel", 18),
        ("DELETE /api/orders/{id}", 21), ("ANY /api/orders/ping", 24),
        ("GET /health", 30),  # second class has no prefix: prefix must reset
    ]


def test_flask_blueprint_prefix_and_methods():
    assert routes("flask/app.py") == [
        ("GET /", 8), ("GET /api/users", 13), ("POST /api/users", 13),
        ("GET /api/users/<int:uid>", 18), ("GET /admin/stats", 23)]


def test_fastapi_router_prefix_and_include():
    assert names("fastapi/main.py") == ["GET /health", "GET /v1/items", "POST /v1/items/{item_id}", "DELETE /v1/items/sub/{x}"]


def test_rails_resources_namespace_scope():
    n = names("rails/config/routes.rb")
    assert n[:2] == ["GET /", "GET /about"]
    assert "GET /admin/users" in n and "GET /admin/users/:id" in n
    assert "POST /admin/users/:id" not in n and "DELETE /admin/users/:id" not in n  # only:
    assert "POST /admin/users/:id/ban" in n and "GET /admin/users/search" in n
    assert "GET /admin/users/:user_id/posts/:id" in n and "DELETE /admin/users/:user_id/posts/:id" not in n
    assert {"GET /api/profile", "PATCH /api/profile", "PUT /api/profile"} <= set(n)
    assert {"GET /api/ping", "HEAD /api/ping"} <= set(n)
    assert sum(x.endswith(" /books") or "/books/" in x for x in n) == 9  # 7 REST + PUT + preview
    assert "GET /books/:id/preview" in n and "DELETE /books/:id" in n


def test_aspnet_attribute_and_minimal_api():
    assert names("aspnet/OrdersController.cs") == [
        "GET /api/Orders", "GET /api/Orders/{id:int}", "POST /api/Orders",
        "DELETE /legacy/orders/{id}", "PUT /api/Orders/Refresh"]
    assert names("aspnet/Program.cs") == ["GET /health", "POST /v1/things"]


def test_deterministic_and_graceful():
    t = (FX / "rails/config/routes.rb").read_text()
    assert extract_routes("config/routes.rb", t) == extract_routes("config/routes.rb", t)
    for rel, junk in (("A.java", "@GetMapping(\"/x"), ("a.py", "@app.get("), ("B.cs", "[HttpGet(\"x"), ("config/routes.rb", "resources :a do\nend\nend\nend")):
        extract_routes(rel, junk)  # must not raise
    assert extract_routes("README.md", "@GetMapping") == []


def test_django_urlconf_and_drf_router():
    assert names("django/urls.py") == [
        "ANY /articles",
        "ANY /articles/feed",
        "ANY /articles/{article_slug}/comments/{comment_pk}",
        "ANY health/",
        "ANY users/<int:pk>/",
        "ANY /tags",
    ]


def test_django_ignores_docstring_and_comment_examples():
    src = '"""Examples:\n    url(r\'^$\', views.home, name=\'home\')\n"""\nurlpatterns = [\n    # url(r\'^old/\', v)\n    url(r\'^real/$\', v),\n]\n'
    assert [f.name for f in extract_routes("proj/urls.py", src)] == ["ANY /real"]

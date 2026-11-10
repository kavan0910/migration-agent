from migration_agent.extract.django_mounts import apply_django_prefix, django_mount_prefixes


def test_nested_includes_and_regex_prefix():
    texts = {
        "conduit/urls.py": "urlpatterns = [\n url(r'^admin/', admin.site.urls),\n url(r'^api/', include('conduit.apps.articles.urls', namespace='a')),\n"
                           " path('v2/', include('conduit.apps.profiles.urls')),\n]\n",
        "conduit/apps/articles/urls.py": "urlpatterns = [url(r'^tags/?$', V.as_view()), path('x/', include('conduit.apps.deep.urls'))]\n",
        "conduit/apps/profiles/urls.py": "urlpatterns = [path('me/', v)]\n",
        "conduit/apps/deep/urls.py": "urlpatterns = [path('y/', v)]\n",
    }
    p = django_mount_prefixes(texts)
    assert p == {"conduit/apps/articles/urls.py": "/api", "conduit/apps/profiles/urls.py": "/v2",
                 "conduit/apps/deep/urls.py": "/api/x"}


def test_ambiguous_double_include_is_left_relative():
    texts = {
        "a/urls.py": "urlpatterns = [path('one/', include('c.urls')), path('two/', include('c.urls'))]\n",
        "c/urls.py": "urlpatterns = [path('z/', v)]\n",
    }
    assert django_mount_prefixes(texts) == {}


def test_apply_is_idempotent_and_handles_verbatim_names():
    assert apply_django_prefix("ANY workshops/", "/api") == "ANY /api/workshops/"
    assert apply_django_prefix("ANY /tags", "/api") == "ANY /api/tags"
    assert apply_django_prefix("ANY /api/tags", "/api") == "ANY /api/tags"
    assert apply_django_prefix("ANY /", "/api") == "ANY /api"

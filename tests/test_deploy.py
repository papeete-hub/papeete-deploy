"""resolve_one()/resolve_versions()/compose() against a fake Registry — no Docker, no network.

IDENTITY AND A QUERY ONLY, RESOLVED AGAINST WHATEVER THE REGISTRY SAYS RIGHT NOW. `FakeRegistry`
below stands in for a real one, so this suite proves the resolution grammar (latest / shortSha /
npm-range, label-embodied) without needing Docker at all — see `test_registry.py` and
`test_e2e_deploy.py` for the real-Docker coverage.
"""
import pytest
import yaml

from papeete_deploy import deploy, k8s


class FakeRegistry:
    def __init__(self, tags: dict[str, list[str]], image_names: dict[str, str] | None = None):
        self._tags = tags
        self._image_names = image_names or {}

    def list_tags(self, name):
        return self._tags.get(name, [])

    def image_name(self, name):
        """None unless the case under test cares — a local daemon's store qualifies nothing."""
        return self._image_names.get(name)


def write_product(tmp_path, actors, environment={"type": "local", "name": "local"}):
    path = tmp_path / "product.yaml"
    path.write_text(yaml.safe_dump(
        {"product": "demo", "version": "0.1.0", "environment": environment, "actors": actors},
        sort_keys=False))
    return path


# ── resolve_one ───────────────────────────────────────────────────────────────────────────────

def test_latest_picks_the_first_tag_embodying_the_label():
    registry = FakeRegistry({
        "archivist": ["0.2.0-alpha-def0000", "0.1.0-alpha-abc0000", "0.1.0-beta-fff0000"],
    })
    assert deploy.resolve_one(registry, "archivist", "alpha", "latest") == "0.2.0-alpha-def0000"


def test_prod_only_matches_bare_semver_tags():
    registry = FakeRegistry({"archivist": ["1.2.3-alpha-abc0000", "1.2.0"]})
    assert deploy.resolve_one(registry, "archivist", "prod", "latest") == "1.2.0"


def test_feature_requires_the_exact_feature_name_as_label():
    registry = FakeRegistry({
        "archivist": ["0.1.0-my-branch-abc0000", "0.1.0-other-branch-def0000"],
    })
    assert (deploy.resolve_one(registry, "archivist", "feature", "latest", "my-branch")
            == "0.1.0-my-branch-abc0000")


def test_a_short_sha_query_matches_by_shortsha_within_the_label():
    registry = FakeRegistry({"archivist": ["0.1.0-alpha-abc0000", "0.2.0-alpha-def1111"]})
    assert deploy.resolve_one(registry, "archivist", "alpha", "abc0000") == "0.1.0-alpha-abc0000"


def test_an_npm_range_query_picks_the_newest_satisfying_tag():
    registry = FakeRegistry({
        "archivist": ["2.0.0-alpha-aaa0000", "1.5.0-alpha-bbb0000", "1.2.0-alpha-ccc0000"],
    })
    assert deploy.resolve_one(registry, "archivist", "alpha", "^1.0.0") == "1.5.0-alpha-bbb0000"


def test_no_matching_tag_is_a_hard_failure():
    registry = FakeRegistry({"archivist": ["1.0.0-beta-abc0000"]})
    with pytest.raises(ValueError):
        deploy.resolve_one(registry, "archivist", "alpha", "latest")


def test_a_beta_query_never_falls_back_to_an_alpha_tag_even_if_newest():
    registry = FakeRegistry({"archivist": ["9.9.9-alpha-zzz0000"]})
    with pytest.raises(ValueError):
        deploy.resolve_one(registry, "archivist", "beta", "latest")


# ── resolve_versions ──────────────────────────────────────────────────────────────────────────

def test_resolve_versions_resolves_every_actor_a_product_names(tmp_path):
    registry = FakeRegistry({
        "customer": ["0.1.0-alpha-aaa0000"],
        "waiter": ["0.1.0-alpha-bbb0000"],
    })
    path = write_product(tmp_path, [
        {"name": "customer", "label": "alpha", "version": "latest"},
        {"name": "waiter", "label": "alpha", "version": "latest"},
    ])
    resolved = deploy.resolve_versions(path, registry)
    assert resolved == [
        {"name": "customer", "version": "0.1.0-alpha-aaa0000"},
        {"name": "waiter", "version": "0.1.0-alpha-bbb0000"},
    ]


# ── compose ───────────────────────────────────────────────────────────────────────────────────

def test_compose_keys_each_service_by_its_normalized_name_and_references_the_resolved_tag():
    resolved = [{"name": "The Archivist", "version": "2.2.0"}]
    services = deploy.compose(resolved, "proj")["services"]
    assert "the-archivist" in services
    svc = services["the-archivist"]
    assert svc["image"] == "the-archivist:2.2.0"
    assert svc["container_name"] == "proj-the-archivist"
    assert svc["ports"] == ["8080"]
    assert "build" not in svc


# ── deploy()/undeploy() dispatch on environment.type ────────────────────────────────────────

def _actor_folder(tmp_path, name, recipe=None):
    """A sibling folder of `tmp_path`'s own product.yaml, named exactly `name` — the default
    local convention (`actor_source.py`) finds it with zero settings."""
    folder = tmp_path / name
    folder.mkdir()
    if recipe:
        (folder / "deploy" / "k8s" / "overlays" / recipe).mkdir(parents=True)
    return folder


def test_deploy_local_delegates_to_up(tmp_path, monkeypatch):
    path = write_product(tmp_path, [{"name": "customer", "label": "alpha", "version": "latest"}])
    calls = []
    monkeypatch.setattr(deploy, "up", lambda p, r: calls.append((p, r)) or "the-project")

    result = deploy.deploy(path, "registry")
    assert result == ("local", "the-project")
    assert calls == [(path, "registry")]


def test_undeploy_local_delegates_to_down(tmp_path, monkeypatch):
    path = write_product(tmp_path, [{"name": "customer", "label": "alpha", "version": "latest"}])
    calls = []
    monkeypatch.setattr(deploy, "down", lambda p: calls.append(p))

    deploy.undeploy(path)
    assert calls == [path]


def test_deploy_k8s_with_no_matching_actor_folder_raises(tmp_path):
    row = {"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"}
    path = write_product(tmp_path, [row],
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx"})
    registry = FakeRegistry({"customer": ["0.1.0-alpha-abc0000"]})
    with pytest.raises(ValueError):
        deploy.deploy(path, registry)  # no sibling 'customer' folder, no settings given


def test_deploy_k8s_with_a_missing_overlay_raises_before_applying_any_actor(tmp_path, monkeypatch):
    _actor_folder(tmp_path, "customer")  # no deploy/k8s/overlays/develop
    _actor_folder(tmp_path, "waiter", recipe="develop")
    rows = [
        {"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"},
        {"name": "waiter", "label": "alpha", "version": "latest", "recipe": "develop"},
    ]
    path = write_product(tmp_path, rows,
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx"})
    registry = FakeRegistry({
        "customer": ["0.1.0-alpha-abc0000"],
        "waiter": ["0.1.0-alpha-def0000"],
    })
    applied = []
    monkeypatch.setattr(k8s, "apply", lambda *a, **kw: applied.append(a))

    with pytest.raises(ValueError):
        deploy.deploy(path, registry)
    assert applied == []


def test_deploy_k8s_happy_path_applies_each_actor(tmp_path, monkeypatch):
    customer = _actor_folder(tmp_path, "customer", recipe="develop")
    waiter = _actor_folder(tmp_path, "waiter", recipe="develop")
    rows = [
        {"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"},
        {"name": "waiter", "label": "alpha", "version": "latest", "recipe": "develop"},
    ]
    path = write_product(tmp_path, rows,
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx"})
    registry = FakeRegistry({
        "customer": ["0.1.0-alpha-abc0000"],
        "waiter": ["0.1.0-alpha-def0000"],
    })
    applied = []
    applied_product = []
    monkeypatch.setattr(k8s, "apply", lambda *a, **kw: applied.append(a))
    monkeypatch.setattr(k8s, "apply_product", lambda *a, **kw: applied_product.append(a))

    result = deploy.deploy(path, registry)
    assert result == ("k8s", "ns")
    assert applied == [
        ("ctx", "ns", customer / "deploy", "develop", "customer", "0.1.0-alpha-abc0000", "demo",
         None),
        ("ctx", "ns", waiter / "deploy", "develop", "waiter", "0.1.0-alpha-def0000", "demo",
         None),
    ]
    # no environment.recipe declared — no product-level deploy folder is even looked for
    assert applied_product == []


def test_deploy_k8s_threads_the_registrys_image_name_through_to_apply(tmp_path, monkeypatch):
    customer = _actor_folder(tmp_path, "customer", recipe="develop")
    rows = [{"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"}]
    path = write_product(tmp_path, rows,
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx"})
    registry = FakeRegistry(
        {"customer": ["0.1.0-alpha-abc0000"]},
        image_names={"customer": "papeetefoundry.azurecr.io/demo/customer"},
    )
    applied = []
    monkeypatch.setattr(k8s, "apply", lambda *a, **kw: applied.append(a))

    deploy.deploy(path, registry)
    assert applied == [
        ("ctx", "ns", customer / "deploy", "develop", "customer", "0.1.0-alpha-abc0000", "demo",
         "papeetefoundry.azurecr.io/demo/customer"),
    ]


def test_deploy_k8s_with_environment_recipe_also_applies_product_level_resources(tmp_path,
                                                                                   monkeypatch):
    customer = _actor_folder(tmp_path, "customer", recipe="develop")
    (tmp_path / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    rows = [{"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"}]
    path = write_product(tmp_path, rows,
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx",
                                       "recipe": "develop"})
    registry = FakeRegistry({"customer": ["0.1.0-alpha-abc0000"]})
    applied = []
    applied_product = []
    monkeypatch.setattr(k8s, "apply", lambda *a, **kw: applied.append(a))
    monkeypatch.setattr(k8s, "apply_product", lambda *a, **kw: applied_product.append(a))

    result = deploy.deploy(path, registry)
    assert result == ("k8s", "ns")
    assert applied_product == [("ctx", "ns", tmp_path / "deploy", "develop", "demo")]
    assert applied == [
        ("ctx", "ns", customer / "deploy", "develop", "customer", "0.1.0-alpha-abc0000", "demo",
         None),
    ]


def test_deploy_k8s_with_environment_recipe_but_missing_product_overlay_raises_before_applying(
        tmp_path, monkeypatch):
    _actor_folder(tmp_path, "customer", recipe="develop")  # a valid actor folder — irrelevant
    rows = [{"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"}]
    path = write_product(tmp_path, rows,
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx",
                                       "recipe": "develop"})
    registry = FakeRegistry({"customer": ["0.1.0-alpha-abc0000"]})
    applied = []
    applied_product = []
    monkeypatch.setattr(k8s, "apply", lambda *a, **kw: applied.append(a))
    monkeypatch.setattr(k8s, "apply_product", lambda *a, **kw: applied_product.append(a))

    with pytest.raises(ValueError):
        deploy.deploy(path, registry)  # no tmp_path/deploy folder at all
    assert applied == []
    assert applied_product == []


def test_undeploy_k8s_delegates_to_k8s_delete(tmp_path, monkeypatch):
    row = {"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"}
    path = write_product(tmp_path, [row],
                          environment={"type": "k8s", "name": "ns", "k8sName": "ctx"})
    calls = []
    monkeypatch.setattr(k8s, "delete", lambda *a: calls.append(a))

    deploy.undeploy(path)
    assert calls == [("ctx", "ns", "demo")]

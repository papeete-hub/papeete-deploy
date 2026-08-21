"""resolve_one()/resolve_versions()/compose() against a fake Registry — no Docker, no network.

IDENTITY AND A QUERY ONLY, RESOLVED AGAINST WHATEVER THE REGISTRY SAYS RIGHT NOW. `FakeRegistry`
below stands in for a real one, so this suite proves the resolution grammar (latest / shortSha /
npm-range, label-embodied) without needing Docker at all — see `test_registry.py` and
`test_e2e_deploy.py` for the real-Docker coverage.
"""
import pytest
import yaml

from papeete_deploy import deploy


class FakeRegistry:
    def __init__(self, tags: dict[str, list[str]]):
        self._tags = tags

    def list_tags(self, name):
        return self._tags.get(name, [])


def write_product(tmp_path, actors, environment="local"):
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

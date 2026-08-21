"""End-to-end: two real actors, spawned via Docker, verified reachable and discoverable.

RUNS REAL CONTAINERS via `deploy.up()` against `examples/product.yaml` — the customer and waiter
actors this repo ships as its own worked example (copied from `papeete-product`'s, so this repo's
tests are self-contained rather than reaching across repos for fixtures).

BUILDING IS NOT THIS PACKAGE'S JOB — that's `papeete-actor build`'s. The `built` fixture below
shells out to plain `docker build` directly, tagging each image in the real
`{semver}-{label}-{shortSha}` shape `examples/product.yaml` declares (`label: alpha, version:
latest`), so `deploy.resolve_one()` has something real to resolve `"latest"` against — standing
in for `papeete-actor build`, exactly the way `papeete-product`'s own (now-deleted)
`test_e2e_product.py` stood in for it before this suite replaced it.

Skipped automatically if no Docker daemon is reachable.
"""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from papeete_deploy import deploy
from papeete_deploy.registry import LocalDockerRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CUSTOMER = EXAMPLES / "customer"
WAITER = EXAMPLES / "waiter"
PRODUCT = EXAMPLES / "product.yaml"

CUSTOMER_TAG = "customer:0.1.0-alpha-e2e0001"
WAITER_TAG = "waiter:0.1.0-alpha-e2e0001"


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _docker_available(), reason="no Docker daemon reachable"),
]


def _get(url: str, timeout: float = 30) -> dict:
    """Poll until the actor's server answers — a fresh container takes a moment to boot."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionError) as e:
            last = e
            time.sleep(0.5)
    raise TimeoutError(f"{url} never answered: {last}")


@pytest.fixture(scope="module")
def built():
    for folder, tag in ((CUSTOMER, CUSTOMER_TAG), (WAITER, WAITER_TAG)):
        subprocess.run(["docker", "build", "-t", tag, str(folder)], check=True)


@pytest.fixture
def running_product(built):
    registry = LocalDockerRegistry()
    proj = deploy.up(PRODUCT, registry)
    try:
        yield proj
    finally:
        deploy.down(PRODUCT, proj)


def test_the_example_product_lints_clean():
    from papeete_product import product as pp
    assert pp.lint(PRODUCT).errors == []


def test_resolve_versions_finds_the_built_images(built):
    resolved = deploy.resolve_versions(PRODUCT, LocalDockerRegistry())
    assert {a["name"]: a["version"] for a in resolved} == {
        "customer": "0.1.0-alpha-e2e0001",
        "waiter": "0.1.0-alpha-e2e0001",
    }


def test_both_actors_are_reachable_from_outside_docker(running_product):
    """REACHABLE: the test process, running on the host, talks to each container over its
    published host port — no Docker network membership required, and each actor answers with
    exactly its own actor.yaml, read at container startup."""
    for name in ("customer", "waiter"):
        host_port = deploy.port(running_product, name)
        body = _get(f"http://localhost:{host_port}/")
        assert body["name"] == name
        assert body["manifest"] == "papeete-actor-manifest/v0"


def test_the_customer_discovers_the_waiter_by_name(running_product):
    """DISCOVERABLE: the customer container, from inside its own network namespace, reaches the
    waiter as `http://waiter:8080/` — a DNS hostname it was never given an IP for."""
    customer_port = deploy.port(running_product, "customer")
    body = _get(f"http://localhost:{customer_port}/order")
    assert body["reached"] == "http://waiter:8080/"
    assert body["waiter_says"]["name"] == "waiter"
    assert body["customer"]["name"] == "customer"

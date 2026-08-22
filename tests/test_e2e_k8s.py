"""End-to-end: two real actors, deployed to a real k8s cluster via `deploy.deploy()`.

RUNS AGAINST WHATEVER `kubectl config current-context` POINTS AT — this machine's real cluster
(verified against Docker Desktop's Kubernetes). Self-skips if no context is configured, the same
portability approach `test_e2e_deploy.py`'s `_docker_available()` uses for Compose e2e.

BUILDING IS NOT THIS PACKAGE'S JOB — the `built` fixture below shells out to plain `docker build`
directly, standing in for `papeete-actor build`, exactly like `test_e2e_deploy.py`'s own fixture.
Only verified against Docker Desktop's Kubernetes, whose node shares the host's local image store
— a cluster with its own separate image store would need those images pushed somewhere reachable
first; that gap is not solved here.
"""
import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

from papeete_deploy import actor_source, deploy
from papeete_deploy.registry import LocalDockerRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CUSTOMER = EXAMPLES / "customer"
WAITER = EXAMPLES / "waiter"

CUSTOMER_TAG = "customer:0.1.0-alpha-e2ek8s1"
WAITER_TAG = "waiter:0.1.0-alpha-e2ek8s1"


def _current_context() -> str | None:
    try:
        result = subprocess.run(["kubectl", "config", "current-context"],
                                 capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


CONTEXT = _current_context()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not CONTEXT, reason="no kubectl context configured"),
]

NAMESPACE = f"papeete-deploy-e2ek8s-{uuid.uuid4().hex[:8]}"

# the temp product.yaml lives elsewhere, so point resolution straight at this repo's own
# examples/ rather than relying on the (inapplicable here) sibling-folder default convention.
ACTOR_SOURCE = actor_source.Settings(
    global_source=actor_source.Source(type="local", root=EXAMPLES))


@pytest.fixture(scope="module")
def built():
    for folder, tag in ((CUSTOMER, CUSTOMER_TAG), (WAITER, WAITER_TAG)):
        subprocess.run(["docker", "build", "-t", tag, str(folder)], check=True)


@pytest.fixture(scope="module")
def product_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("e2ek8s") / "product.yaml"
    path.write_text(yaml.safe_dump({
        "product": "table-service-e2ek8s",
        "version": "0.1.0",
        "environment": {"type": "k8s", "name": NAMESPACE, "k8sName": CONTEXT},
        "actors": [
            {"name": "customer", "label": "alpha", "version": "latest", "recipe": "develop"},
            {"name": "waiter", "label": "alpha", "version": "latest", "recipe": "develop"},
        ],
    }))
    return path


@pytest.fixture
def deployed_product(built, product_path):
    registry = LocalDockerRegistry()
    env_type, namespace = deploy.deploy(product_path, registry, actor_source=ACTOR_SOURCE)
    try:
        subprocess.run(
            ["kubectl", "--context", CONTEXT, "-n", namespace, "wait", "--for=condition=Ready",
             "pod", "-l", "papeete-deploy/product=table-service-e2ek8s", "--timeout=60s"],
            check=True,
        )
        yield env_type, namespace
    finally:
        deploy.undeploy(product_path)


def _kubectl_exec(namespace: str, pod_selector: str, *cmd: str, retries: int = 10) -> str:
    """`kubectl exec` into the first pod matching `pod_selector`, retrying briefly — a Service's
    Endpoints/CoreDNS entry can lag a moment behind `wait --for=condition=Ready`, which only
    confirms the container itself, not cluster-DNS propagation for a Service created the same
    instant."""
    pod = subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", namespace, "get", "pod",
         "-l", pod_selector, "-o", "jsonpath={.items[0].metadata.name}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    for attempt in range(retries):
        result = subprocess.run(
            ["kubectl", "--context", CONTEXT, "-n", namespace, "exec", pod, "--", *cmd],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout
        if attempt == retries - 1:
            result.check_returncode()
        time.sleep(1)


def test_both_actors_become_ready(deployed_product):
    env_type, namespace = deployed_product
    assert env_type == "k8s"
    assert namespace == NAMESPACE


def test_the_customer_discovers_the_waiter_by_name(deployed_product):
    """DISCOVERABLE: the customer pod, from inside the cluster, reaches the waiter Service as
    `http://waiter:8080/` — a DNS hostname it was never given an IP for."""
    _env_type, namespace = deployed_product
    out = _kubectl_exec(namespace, "app=customer", "python", "-c",
                         "import urllib.request; print(urllib.request.urlopen("
                         "'http://localhost:8080/order', timeout=5).read().decode())")
    body = json.loads(out)
    assert body["reached"] == "http://waiter:8080/"
    assert body["waiter_says"]["name"] == "waiter"
    assert body["customer"]["name"] == "customer"


def _resource_count(namespace: str, product_name: str) -> int:
    out = subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", namespace, "get", "all",
         "-l", f"papeete-deploy/product={product_name}", "-o", "name"],
        check=True, capture_output=True, text=True,
    ).stdout
    return len([line for line in out.splitlines() if line])


def _namespace_exists(namespace: str) -> bool:
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, "get", "namespace", namespace],
        capture_output=True,
    ).returncode == 0


def test_undeploy_removes_resources_but_leaves_the_namespace(built, product_path):
    registry = LocalDockerRegistry()
    _env_type, namespace = deploy.deploy(product_path, registry, actor_source=ACTOR_SOURCE)
    subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", namespace, "wait", "--for=condition=Ready",
         "pod", "-l", "papeete-deploy/product=table-service-e2ek8s", "--timeout=60s"],
        check=True,
    )
    assert _resource_count(namespace, "table-service-e2ek8s") > 0

    deploy.undeploy(product_path)

    assert _resource_count(namespace, "table-service-e2ek8s") == 0
    assert _namespace_exists(namespace)

"""LocalDockerRegistry against real local images — needs a Docker daemon; skipped otherwise.

AcrRegistry's `image_name()` is pure string composition and is covered here offline. Its
`list_tags()` still is not: that one needs a live registry to mean anything (`registry.py`,
`ADR-PD-0001`).
"""
import subprocess
import time

import pytest

from papeete_deploy.registry import AcrRegistry, LocalDockerRegistry


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(not _docker_reachable(),
                                     reason="no Docker daemon reachable")

REPO = "papeete-deploy-test-registry-fixture"
OLDER = f"{REPO}:0.1.0-alpha-aaa0000"
NEWER = f"{REPO}:0.2.0-alpha-bbb0000"


@pytest.fixture(scope="module")
def tagged_images(tmp_path_factory):
    """Two genuinely distinct images (a differing LABEL forces distinct image IDs, so each gets
    its own CreatedAt) tagged under the same repository, a beat apart — enough for `list_tags()`
    to have a real newest-first order to prove."""
    tmp = tmp_path_factory.mktemp("registry-fixture")
    dockerfile = tmp / "Dockerfile"

    dockerfile.write_text("FROM scratch\nLABEL fixture=aaa0000\n")
    subprocess.run(["docker", "build", "-t", OLDER, str(tmp)], check=True)
    time.sleep(1.1)  # CreatedAt has 1-second resolution
    dockerfile.write_text("FROM scratch\nLABEL fixture=bbb0000\n")
    subprocess.run(["docker", "build", "-t", NEWER, str(tmp)], check=True)

    yield

    subprocess.run(["docker", "rmi", OLDER, NEWER], capture_output=True)


@requires_docker
def test_list_tags_returns_every_tag_newest_first(tagged_images):
    assert LocalDockerRegistry().list_tags(REPO) == ["0.2.0-alpha-bbb0000", "0.1.0-alpha-aaa0000"]


def test_local_registry_has_no_image_name_to_rewrite_to():
    assert LocalDockerRegistry().image_name("customer") is None


# ── AcrRegistry.image_name — offline ─────────────────────────────────────────────────────────

def test_acr_image_name_composes_login_server_prefix_and_normalized_name():
    registry = AcrRegistry("papeetefoundry", repository_prefix="foundry")

    assert registry.image_name("BNK.RLVR.CAP.SUP.002.BEN-implementation") == (
        "papeetefoundry.azurecr.io/foundry/bnk.rlvr.cap.sup.002.ben-implementation")


def test_acr_image_name_carries_no_tag():
    # the tag is resolved separately and applied by the same kustomize images entry's newTag
    assert ":" not in AcrRegistry("papeetefoundry", "foundry").image_name("customer")


def test_acr_image_name_without_a_prefix_is_just_the_repository():
    assert AcrRegistry("papeetefoundry").image_name("customer") == (
        "papeetefoundry.azurecr.io/customer")


def test_list_tags_is_empty_for_an_unknown_repository():
    assert LocalDockerRegistry().list_tags("definitely-not-a-real-repo-xyz") == []

"""k8s.py's pure/offline pieces — no kubectl, no cluster. Live-cluster coverage is
`test_e2e_k8s.py`'s job.
"""
import os

import pytest
import yaml

from papeete_deploy import k8s


# ── _wrapper_kustomization ───────────────────────────────────────────────────────────────────

def test_wrapper_kustomization_sets_resources_images_and_labels(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(overlay_dir, "customer", "0.1.0-alpha-abc0000",
                                             "table-service")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    # a relative path from the wrapper dir to the overlay — never an absolute one, which
    # kustomize's root-reference check rejects outright regardless of load-restrictor settings.
    [resource] = kustomization["resources"]
    assert not os.path.isabs(resource)
    assert (wrapper_dir / resource).resolve() == overlay_dir.resolve()
    assert kustomization["images"] == [{"name": "customer", "newTag": "0.1.0-alpha-abc0000"}]
    assert kustomization["commonLabels"] == {
        k8s.MANAGED_BY_LABEL: "papeete-deploy",
        k8s.PRODUCT_LABEL: "table-service",
    }


# ── _overlay_dir ──────────────────────────────────────────────────────────────────────────────

def test_overlay_dir_raises_when_the_overlay_is_missing(tmp_path):
    with pytest.raises(ValueError):
        k8s._overlay_dir(tmp_path, "develop")


def test_overlay_dir_resolves_the_path_when_the_overlay_exists(tmp_path):
    overlay = tmp_path / "k8s" / "overlays" / "develop"
    overlay.mkdir(parents=True)
    assert k8s._overlay_dir(tmp_path, "develop") == overlay

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
                                             "table-service", "develop-ns")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    # a relative path from the wrapper dir to the overlay — never an absolute one, which
    # kustomize's root-reference check rejects outright regardless of load-restrictor settings.
    [resource] = kustomization["resources"]
    assert not os.path.isabs(resource)
    assert (wrapper_dir / resource).resolve() == overlay_dir.resolve()
    assert kustomization["namePrefix"] == "table-service-"
    assert kustomization["images"] == [{"name": "customer", "newTag": "0.1.0-alpha-abc0000"}]
    assert kustomization["commonLabels"] == {
        k8s.MANAGED_BY_LABEL: "papeete-deploy",
        k8s.PRODUCT_LABEL: "table-service",
    }


def test_wrapper_kustomization_emits_new_name_alongside_new_tag(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(
        overlay_dir, "customer", "0.1.0-alpha-abc0000", "table-service", "develop-ns",
        "papeetefoundry.azurecr.io/table-service/customer")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    # the base manifest still names its container "customer" — newName is what redirects it to a
    # registry, so the actor's own files never learn which one they were deployed against.
    assert kustomization["images"] == [{
        "name": "customer",
        "newName": "papeetefoundry.azurecr.io/table-service/customer",
        "newTag": "0.1.0-alpha-abc0000",
    }]


def test_wrapper_kustomization_omits_new_name_when_none_given(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(overlay_dir, "customer", "0.1.0-alpha-abc0000",
                                             "table-service", "develop-ns")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    assert kustomization["images"] == [{"name": "customer", "newTag": "0.1.0-alpha-abc0000"}]


def test_wrapper_kustomization_omits_images_when_no_image_given(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(overlay_dir, None, None, "table-service",
                                              "develop-ns")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    assert "images" not in kustomization
    assert kustomization["namePrefix"] == "table-service-"
    assert kustomization["commonLabels"] == {
        k8s.MANAGED_BY_LABEL: "papeete-deploy",
        k8s.PRODUCT_LABEL: "table-service",
    }


def test_wrapper_kustomization_normalizes_the_prefix_but_not_the_label(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(overlay_dir, None, None, "Table Service",
                                              "develop-ns")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    assert kustomization["namePrefix"] == "table-service-"
    assert kustomization["commonLabels"][k8s.PRODUCT_LABEL] == "Table Service"


# ── ADR-PD-0005: generated Ingress path prefix ──────────────────────────────────────────────────

def test_wrapper_kustomization_injects_an_ingress_prefix_configmap_and_replacement(tmp_path):
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    wrapper_dir = k8s._wrapper_kustomization(overlay_dir, None, None, "Table Service",
                                              "develop-ns")
    kustomization = yaml.safe_load((wrapper_dir / "kustomization.yaml").read_text())

    [configmap] = kustomization["configMapGenerator"]
    assert configmap["name"] == k8s.INGRESS_PREFIX_CONFIGMAP_NAME
    # the product segment is normalized (matches namePrefix), the namespace segment is used as-is
    # (already k8s-namespace-safe, hence URL-segment-safe).
    assert configmap["literals"] == ["PATH_PREFIX=/table-service/develop-ns"]

    [replacement] = kustomization["replacements"]
    assert replacement["source"] == {
        "kind": "ConfigMap",
        "name": k8s.INGRESS_PREFIX_CONFIGMAP_NAME,
        "fieldPath": "data.PATH_PREFIX",
    }
    [target] = replacement["targets"]
    assert target["select"] == {"kind": "Ingress"}
    assert target["fieldPaths"] == ["spec.rules.*.http.paths.*.path"]
    # index: 0 on a "/"-delimited, leading-slash path replaces the empty pre-leading-slash
    # element — a clean prefix, not a whole-field clobber (verified live, ADR-PD-0005).
    assert target["options"] == {"delimiter": "/", "index": 0}


# ── _overlay_dir ──────────────────────────────────────────────────────────────────────────────

def test_overlay_dir_raises_when_the_overlay_is_missing(tmp_path):
    with pytest.raises(ValueError):
        k8s._overlay_dir(tmp_path, "develop")


def test_overlay_dir_resolves_the_path_when_the_overlay_exists(tmp_path):
    overlay = tmp_path / "k8s" / "overlays" / "develop"
    overlay.mkdir(parents=True)
    assert k8s._overlay_dir(tmp_path, "develop") == overlay

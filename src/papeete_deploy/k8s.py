"""Deploying to Kubernetes — an actor's own kustomize overlay, with the resolved image injected.

WHERE THIS PICKS UP. `papeete-actor`'s `ADR-PA-0025` lets an actor's folder carry
`deploy/k8s/base/` + `deploy/k8s/overlays/<name>/` — a plain kustomize layout, actor-authored,
with the base Deployment's container image named exactly the actor's own normalized name, no tag.
This module never edits an actor's own files: it wraps the chosen overlay in a fresh temporary
kustomization directory that only sets the image's tag and adds a couple of labels, then applies
that wrapper — the same never-mutate-the-source discipline `deploy.py`'s `_compose_file()` already
uses (via `tempfile.NamedTemporaryFile`) for Compose.

NEVER DELETES A NAMESPACE. `ensure_namespace()` creates one if missing and never removes it;
`delete()` tears down only the resources it (by label) knows it created.
"""
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
PRODUCT_LABEL = "papeete-deploy/product"


def _kubectl(context: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", "--context", context, *args], check=True, **kwargs)


def ensure_namespace(context: str, namespace: str) -> None:
    """Create `namespace` on `context` if missing — idempotent, NEVER deletes."""
    exists = subprocess.run(
        ["kubectl", "--context", context, "get", "namespace", namespace],
        capture_output=True,
    ).returncode == 0
    if not exists:
        _kubectl(context, "create", "namespace", namespace)


def _overlay_dir(deploy_folder: Path, recipe: str) -> Path:
    """`<deploy_folder>/k8s/overlays/<recipe>` — ValueError if missing, no fallback.
    `deploy_folder` is already an actor's own deploy folder — see `actor_source.py`, which is
    the one place that ever joins a `deploy` segment onto an actor's own folder now."""
    overlay = Path(deploy_folder) / "k8s" / "overlays" / recipe
    if not overlay.is_dir():
        raise ValueError(f"{deploy_folder}: no 'k8s/overlays/{recipe}' overlay")
    return overlay


def _wrapper_kustomization(overlay_dir: Path, image_name: str, resolved_version: str,
                            product_name: str) -> Path:
    """A fresh temp dir holding one kustomization.yaml: resources=[a path to `overlay_dir`,
    relative to the wrapper dir — the actor's own files are never copied or edited, and
    kustomize's root-reference check rejects an absolute one outright], images=[{name: image_name,
    newTag: resolved_version}], commonLabels={MANAGED_BY_LABEL: "papeete-deploy", PRODUCT_LABEL:
    product_name}. Pure string/YAML construction — offline-testable, same tempfile discipline as
    deploy.py's `_compose_file()`."""
    wrapper_dir = Path(tempfile.mkdtemp())
    resource = os.path.relpath(Path(overlay_dir).resolve(), wrapper_dir)
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [resource],
        "images": [{"name": image_name, "newTag": resolved_version}],
        "commonLabels": {
            MANAGED_BY_LABEL: "papeete-deploy",
            PRODUCT_LABEL: product_name,
        },
    }
    (wrapper_dir / "kustomization.yaml").write_text(yaml.safe_dump(kustomization))
    return wrapper_dir


def apply(context: str, namespace: str, deploy_folder: Path, recipe: str, image_name: str,
          resolved_version: str, product_name: str) -> None:
    """ensure_namespace(), then apply the wrapper kustomization's rendered manifest.

    Renders via `kubectl kustomize` rather than `kubectl apply -k` directly: the wrapper's
    `resources` entry points outside the wrapper's own tempdir (deliberately — the actor's overlay
    is never copied), and `apply -k`'s built-in loader refuses that by default
    (`LoadRestrictionsRootOnly`, rejects anything outside the kustomization root).
    `--load-restrictor=LoadRestrictionsNone` lifts that for the render step only; the resulting
    plain manifest is then applied the ordinary way.
    """
    ensure_namespace(context, namespace)
    overlay = _overlay_dir(deploy_folder, recipe)
    wrapper_dir = _wrapper_kustomization(overlay, image_name, resolved_version, product_name)
    manifest = subprocess.run(
        ["kubectl", "kustomize", str(wrapper_dir), "--load-restrictor=LoadRestrictionsNone"],
        check=True, capture_output=True, text=True,
    ).stdout
    _kubectl(context, "-n", namespace, "apply", "-f", "-", input=manifest, text=True)


def delete(context: str, namespace: str, product_name: str) -> None:
    """`kubectl --context CONTEXT -n NAMESPACE delete all,configmap,ingress -l
    PRODUCT_LABEL=product_name` — everything an actor's overlay might have defined, by label,
    never the namespace itself."""
    _kubectl(context, "-n", namespace, "delete", "all,configmap,ingress",
             "-l", f"{PRODUCT_LABEL}={product_name}")

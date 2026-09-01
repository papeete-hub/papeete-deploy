"""Deploying to Kubernetes — an actor's own kustomize overlay, with the resolved image injected.

WHERE THIS PICKS UP. `papeete-actor`'s `ADR-PA-0025` lets an actor's folder carry
`deploy/k8s/base/` + `deploy/k8s/overlays/<name>/` — a plain kustomize layout, actor-authored,
with the base Deployment's container image named exactly the actor's own normalized name, no tag.
This module never edits an actor's own files: it wraps the chosen overlay in a fresh temporary
kustomization directory that sets the image's tag, adds a couple of labels, and (ADR-PD-0005)
prefixes every Ingress path with `/<product>/<namespace>`, then applies that wrapper — the same
never-mutate-the-source discipline `deploy.py`'s `_compose_file()` already uses (via
`tempfile.NamedTemporaryFile`) for Compose.

NEVER DELETES A NAMESPACE. `ensure_namespace()` creates one if missing and never removes it;
`delete()` tears down only the resources it (by label) knows it created.
"""
import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from papeete_version.version import normalize_name

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


INGRESS_PREFIX_CONFIGMAP_NAME = "papeete-deploy-ingress-prefix"


def _ingress_prefix_configmap(product_name: str, namespace: str) -> dict:
    """A configMapGenerator entry holding one literal: `/<normalized product>/<namespace>` — the
    only vehicle `replacements` has for a literal value, since its `source` must reference a field
    on an actual object, not an inline string (ADR-PD-0005). Picks up the wrapper's own
    `commonLabels` like every other generated object, so `delete()`'s existing label sweep
    (`all,configmap,ingress`) reclaims it with no separate handling."""
    return {
        "name": INGRESS_PREFIX_CONFIGMAP_NAME,
        "literals": [f"PATH_PREFIX=/{normalize_name(product_name)}/{namespace}"],
    }


_INGRESS_PREFIX_REPLACEMENT = {
    "source": {
        "kind": "ConfigMap",
        "name": INGRESS_PREFIX_CONFIGMAP_NAME,
        "fieldPath": "data.PATH_PREFIX",
    },
    "targets": [{
        "select": {"kind": "Ingress"},
        # every Ingress path is, by k8s API validation, an absolute path starting with "/" — so
        # splitting on "/" always yields an empty element at index 0. Replacing that empty
        # element with the full "/<product>/<namespace>" literal is a clean prefix, not a
        # whole-field clobber (verified live against kustomize v5.7.1, ADR-PD-0005). A path
        # authored without its required leading "/" would instead have its real first segment
        # silently replaced — not a new constraint, the k8s API already requires the leading "/".
        "fieldPaths": ["spec.rules.*.http.paths.*.path"],
        "options": {"delimiter": "/", "index": 0},
    }],
}


def _wrapper_kustomization(overlay_dir: Path, image_name: str | None, resolved_version: str | None,
                            product_name: str, namespace: str, new_name: str | None = None) -> Path:
    """A fresh temp dir holding one kustomization.yaml: resources=[a path to `overlay_dir`,
    relative to the wrapper dir — the actor's own files are never copied or edited, and
    kustomize's root-reference check rejects an absolute one outright], namePrefix=the product's
    own normalized name (so every resource this wrapper renders is product-scoped — the k8s
    analogue of Compose's `f"{project}-{name}"` container naming, and kustomize's built-in
    nameReference transformer keeps in-overlay references like an Ingress's backend.service.name
    correct automatically), images=[{name: image_name, newTag: resolved_version}] (omitted
    entirely when `image_name` is None — a product-level resource has no container to retag),
    commonLabels={MANAGED_BY_LABEL: "papeete-deploy", PRODUCT_LABEL: product_name} (PRODUCT_LABEL
    stays the raw, unnormalized product name — only namePrefix is normalized). Pure string/YAML
    construction — offline-testable, same tempfile discipline as deploy.py's `_compose_file()`.

    ALSO (ADR-PD-0005): a configMapGenerator + replacements block that prefixes every rendered
    Ingress path with `/<normalized product_name>/<namespace>`, unconditionally — see
    `_ingress_prefix_configmap()`/`_INGRESS_PREFIX_REPLACEMENT` above for the mechanism. An actor's
    own `ingress.yaml` owns only its bare, actor-local path segment; this wrapper generates the
    rest, the same way it already generates `namePrefix` for object names instead of trusting an
    author to hand-type a product/environment prefix that stays in sync.

    AND (ADR-PD-0006): `new_name`, when given, becomes the image entry's `newName` alongside
    `newTag` — the registry an actor's image is actually pulled from. It is generated here for
    the same reason the ingress prefix is: welding a registry host into an actor's own base
    manifest would make that manifest deployable to exactly one environment, which is what the
    wrap-never-edit discipline exists to prevent. None (a local daemon's store) leaves the
    image's own name untouched."""
    wrapper_dir = Path(tempfile.mkdtemp())
    resource = os.path.relpath(Path(overlay_dir).resolve(), wrapper_dir)
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namePrefix": f"{normalize_name(product_name)}-",
        "resources": [resource],
        "commonLabels": {
            MANAGED_BY_LABEL: "papeete-deploy",
            PRODUCT_LABEL: product_name,
        },
        "configMapGenerator": [_ingress_prefix_configmap(product_name, namespace)],
        "replacements": [_INGRESS_PREFIX_REPLACEMENT],
    }
    if image_name is not None:
        image = {"name": image_name, "newTag": resolved_version}
        if new_name is not None:
            image["newName"] = new_name
        kustomization["images"] = [image]
    (wrapper_dir / "kustomization.yaml").write_text(yaml.safe_dump(kustomization))
    return wrapper_dir


def _render(wrapper_dir: Path) -> str:
    """Render a wrapper kustomization to a plain manifest via `kubectl kustomize` rather than
    `kubectl apply -k` directly: the wrapper's `resources` entry points outside the wrapper's own
    tempdir (deliberately — the actor's/product's own overlay is never copied), and `apply -k`'s
    built-in loader refuses that by default (`LoadRestrictionsRootOnly`, rejects anything outside
    the kustomization root). `--load-restrictor=LoadRestrictionsNone` lifts that for the render
    step only; the resulting plain manifest is then applied the ordinary way."""
    return subprocess.run(
        ["kubectl", "kustomize", str(wrapper_dir), "--load-restrictor=LoadRestrictionsNone"],
        check=True, capture_output=True, text=True,
    ).stdout


def apply(context: str, namespace: str, deploy_folder: Path, recipe: str, image_name: str,
          resolved_version: str, product_name: str, new_name: str | None = None) -> None:
    """ensure_namespace(), then apply the wrapper kustomization's rendered manifest. `new_name`
    is the registry-qualified repository to pull from, or None to leave the manifest's own image
    name alone — see `_wrapper_kustomization()`."""
    ensure_namespace(context, namespace)
    overlay = _overlay_dir(deploy_folder, recipe)
    wrapper_dir = _wrapper_kustomization(overlay, image_name, resolved_version, product_name,
                                          namespace, new_name)
    manifest = _render(wrapper_dir)
    _kubectl(context, "-n", namespace, "apply", "-f", "-", input=manifest, text=True)


def apply_product(context: str, namespace: str, deploy_folder: Path, recipe: str,
                   product_name: str) -> None:
    """Apply the PRODUCT-level deploy folder's own overlay — same wrap-never-edit discipline as
    `apply()`, minus image injection (a product-level resource, e.g. a shared dashboard ConfigMap,
    has no container to retag). Carries the same PRODUCT_LABEL via `_wrapper_kustomization()`, so
    `delete()` tears it down identically to any actor's resources — no separate teardown path."""
    ensure_namespace(context, namespace)
    overlay = _overlay_dir(deploy_folder, recipe)
    wrapper_dir = _wrapper_kustomization(overlay, None, None, product_name, namespace)
    manifest = _render(wrapper_dir)
    _kubectl(context, "-n", namespace, "apply", "-f", "-", input=manifest, text=True)


def delete(context: str, namespace: str, product_name: str) -> None:
    """`kubectl --context CONTEXT -n NAMESPACE delete all,configmap,ingress -l
    PRODUCT_LABEL=product_name` — everything an actor's overlay might have defined, by label,
    never the namespace itself."""
    _kubectl(context, "-n", namespace, "delete", "all,configmap,ingress",
             "-l", f"{PRODUCT_LABEL}={product_name}")

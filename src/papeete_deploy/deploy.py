"""Resolving a product's declared version queries against a real registry, and deploying it.

WHERE THIS PICKS UP. `papeete-product`'s `product.yaml` names each actor by identity and a query
(`label`/`version`/`featureName`, `papeete-version`'s vocabulary) — never a location, never a
pre-resolved tag (`ADR-PP-0002`). This module folds that query against whatever `Registry` it is
given (`registry.py`) to find the concrete tag it means, then drives Docker Compose from the
resolved set — starting one already-built image per actor on one shared, per-project network,
where every actor is reachable by every other under its `name` as a DNS hostname. Docker's own
embedded network DNS does the discovery; nothing new is invented for it.

NEVER BUILDS. Building one actor is `papeete-actor build`'s job, a different repo's
responsibility — this module only ever consumes tags a registry already has.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from papeete_product import product as pp
from papeete_version import npm_range
from papeete_version import version as pv

from .registry import Registry

PORT = 8080
_SHORTSHA = re.compile(r"^[0-9a-f]{7,40}$")


def normalize(name: str) -> str:
    """A `name` as a DNS-safe, Docker-tag-safe hostname."""
    return pv.normalize_name(name)


def image_tag(name: str, version: str) -> str:
    return f"{normalize(name)}:{version}"


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


# ── resolution ────────────────────────────────────────────────────────────────────────────────

def _label_of(tag_version: str) -> str | None:
    """The label a resolved tag carries — everything between the semver core and the trailing
    shortSha. `None` for a bare `X.Y.Z` (the `prod`/GA shape)."""
    parts = tag_version.split("-")
    if len(parts) < 3:
        return None
    return "-".join(parts[1:-1])


def _semver_core_of(tag_version: str) -> npm_range.SemVer:
    return npm_range.parse_semver(tag_version.split("-", 1)[0])


def _expected_label(label: str, feature_name: str | None) -> str | None:
    if label == "prod":
        return None
    return feature_name if label == "feature" else label


def resolve_one(registry: Registry, name: str, label: str, version: str,
                 feature_name: str | None = None) -> str:
    """Fold `{label, version, featureName}` against every tag `registry.list_tags(name)` has —
    the same query grammar `papeete-version match-version` accepts (`"latest"` / a short SHA /
    an npm-range), but picking the newest match from a registry's actual tag list instead of one
    git-computed 'live' value. No match is never fabricated — a hard `ValueError`, same
    no-silent-fallback discipline as the rest of the ecosystem.
    """
    tags = registry.list_tags(name)
    expected = _expected_label(label, feature_name)
    candidates = [t for t in tags if _label_of(t) == expected]

    if version == "latest":
        if not candidates:
            raise ValueError(f"{name}: no tag embodies label '{label}'"
                              + (f" feature '{feature_name}'" if feature_name else ""))
        return candidates[0]

    if _SHORTSHA.match(version):
        for t in candidates:
            sha = t.rsplit("-", 1)[-1]
            if sha == version or sha.startswith(version):
                return t
        raise ValueError(f"{name}: no tag for label '{label}' has short SHA '{version}'")

    for t in candidates:
        if npm_range.satisfies(_semver_core_of(t), version):
            return t
    raise ValueError(f"{name}: no tag for label '{label}' satisfies '{version}'")


def resolve_versions(product_path: Path | str, registry: Registry) -> list[dict]:
    """Each actor `product.yaml` names, its declared query resolved against `registry` — the
    concrete `{name, version}` this deployment will actually use."""
    resolved = []
    for actor in pp.resolve(product_path):
        version = resolve_one(registry, actor["name"], actor["label"], actor["version"],
                               actor.get("featureName"))
        resolved.append({"name": actor["name"], "version": version})
    return resolved


# ── orchestration ─────────────────────────────────────────────────────────────────────────────

def project_name(path: Path | str) -> str:
    product = yaml.safe_load(Path(path).read_text())
    return normalize(product["product"])


def compose(resolved_actors: list[dict], project: str) -> dict:
    """The docker-compose shape for an already-resolved actor list — one service per actor,
    keyed by its `name` (normalized), referencing the resolved `<name>:<version>` image."""
    services = {}
    for actor in resolved_actors:
        name = normalize(actor["name"])
        services[name] = {
            "image": image_tag(actor["name"], actor["version"]),
            "container_name": f"{project}-{name}",
            "ports": [str(PORT)],
        }
    return {"services": services}


def _compose_file(resolved_actors: list[dict], project: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(compose(resolved_actors, project), f)
        return f.name


def up(product_path: Path | str, registry: Registry, project: str | None = None) -> str:
    """Resolve every actor a product names against `registry`, then start them all. Returns the
    Compose project name."""
    product_path = Path(product_path)
    project = project or project_name(product_path)
    resolved = resolve_versions(product_path, registry)
    compose_path = _compose_file(resolved, project)
    subprocess.run(["docker", "compose", "-f", compose_path, "-p", project, "up", "-d"], check=True)
    return project


def down(product_path: Path | str, project: str | None = None) -> None:
    """Tear down what `up` started. Needs only the project name — Compose tracks its own
    containers by project label, so no compose file has to survive between the two calls."""
    project = project or project_name(product_path)
    subprocess.run(["docker", "compose", "-p", project, "down", "--remove-orphans"], check=True)


def port(project: str, name: str, container_port: int = PORT) -> int:
    """The host port Docker published `name`'s `container_port` to, after `up()`."""
    container = f"{project}-{normalize(name)}"
    out = subprocess.run(
        ["docker", "port", container, str(container_port)],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    return int(out.rsplit(":", 1)[-1])

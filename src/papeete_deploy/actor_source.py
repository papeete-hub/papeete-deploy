"""Locating an actor's own deploy folder for a k8s deploy — configurable, not just a CLI argument.

THREE TIERS, MOST TO LEAST SPECIFIC. A per-actor override (`papeete-deploy.yaml`'s
`actorDeployOverrides`, config-file only — a list of mappings doesn't fit a CLI flag or an env
var) beats the GLOBAL source, which itself is layered CLI flag > env var > config file's
`actorDeploySource` > a zero-config LOCAL CONVENTION: an actor's folder sits as a sibling of
`product.yaml`, named exactly the actor's own declared name — no reading `actor.yaml` to match it,
folder name IS the match.

EVERY PATH RESOLVES TO THE ACTOR'S OWN *DEPLOY* FOLDER, not its root — local and git resolution
both land one level inside the actor's own folder, at `.../deploy`, so `k8s._overlay_dir()` only
ever has to look for `k8s/overlays/<recipe>` under what this module hands it.
"""
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILENAME = "papeete-deploy.yaml"

ENV_SOURCE = "PAPEETE_DEPLOY_ACTOR_SOURCE"
ENV_ROOT = "PAPEETE_DEPLOY_ACTOR_ROOT"
ENV_GIT_URL = "PAPEETE_DEPLOY_ACTOR_GIT_URL"
ENV_GIT_REF = "PAPEETE_DEPLOY_ACTOR_GIT_REF"


@dataclass
class Source:
    type: str                      # "local" | "git"
    root: Path | None = None       # local: the folder containing <actor-name> subfolders
    path: Path | None = None       # local, per-actor override only: the actor's own folder
    url: str | None = None         # git
    ref: str | None = None         # git, optional
    subpath: str | None = None     # git, per-actor override only; default: the actor's own name


@dataclass
class Settings:
    global_source: Source
    overrides: dict[str, Source] = field(default_factory=dict)


def _source_from_mapping(m: dict) -> Source:
    type_ = m.get("type")
    if type_ not in ("local", "git"):
        raise ValueError(f"actor deploy source: 'type' must be 'local' or 'git', got {type_!r}")
    if type_ == "git" and not m.get("url"):
        raise ValueError("actor deploy source: 'url' is required when type is 'git'")
    return Source(
        type=type_,
        root=Path(m["root"]) if m.get("root") else None,
        path=Path(m["path"]) if m.get("path") else None,
        url=m.get("url"),
        ref=m.get("ref"),
        subpath=m.get("subpath"),
    )


def _load_config_file(product_path: Path) -> tuple[Source | None, dict[str, Source]]:
    config_path = product_path.parent / CONFIG_FILENAME
    if not config_path.is_file():
        return None, {}
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{config_path}: does not parse: {e}")

    global_source = None
    if "actorDeploySource" in config:
        global_source = _source_from_mapping(config["actorDeploySource"])

    overrides = {}
    for row in config.get("actorDeployOverrides") or []:
        actor = row.get("actor")
        if not actor:
            raise ValueError(f"{config_path}: an actorDeployOverrides entry is missing 'actor'")
        overrides[actor] = _source_from_mapping(row)

    return global_source, overrides


def load_settings(product_path: Path | str, *, cli_type: str | None = None,
                   cli_root: Path | str | None = None, cli_git_url: str | None = None,
                   cli_git_ref: str | None = None) -> Settings:
    """CLI flag > env var > config file's `actorDeploySource` > local convention (root =
    `product_path`'s own parent directory). `overrides` come only from the config file."""
    product_path = Path(product_path)
    file_source, overrides = _load_config_file(product_path)

    type_ = cli_type or os.environ.get(ENV_SOURCE) or (file_source.type if file_source else None) \
        or "local"
    root = cli_root or os.environ.get(ENV_ROOT) or (file_source.root if file_source else None) \
        or product_path.parent
    url = cli_git_url or os.environ.get(ENV_GIT_URL) or (file_source.url if file_source else None)
    ref = cli_git_ref or os.environ.get(ENV_GIT_REF) or (file_source.ref if file_source else None)

    if type_ == "git" and not url:
        raise ValueError(
            f"actor deploy source is 'git' but no URL was given "
            f"(--actor-git-url / {ENV_GIT_URL} / {CONFIG_FILENAME}'s actorDeploySource.url)"
        )

    global_source = Source(type=type_, root=Path(root), url=url, ref=ref)
    return Settings(global_source=global_source, overrides=overrides)


def _clone(url: str, ref: str | None, clones: dict[tuple[str, str | None], Path]) -> Path:
    key = (url, ref)
    if key in clones:
        return clones[key]
    dest = Path(tempfile.mkdtemp())
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise ValueError(f"failed to clone {url}@{ref or 'default branch'}: {e.stderr}")
    clones[key] = dest
    return dest


def resolve_actor_folder(actor_name: str, settings: Settings,
                          clones: dict[tuple[str, str | None], Path] | None = None) -> Path:
    """The actor's own DEPLOY folder — `settings.overrides[actor_name]` if present, else
    `settings.global_source`. ValueError, no traceback, if the resolved path doesn't exist."""
    clones = clones if clones is not None else {}
    source = settings.overrides.get(actor_name, settings.global_source)

    if source.type == "local":
        actor_folder = source.path if source.path else Path(source.root) / actor_name
        deploy_folder = Path(actor_folder) / "deploy"
    else:
        clone_dir = _clone(source.url, source.ref, clones)
        deploy_folder = clone_dir / (source.subpath or actor_name) / "deploy"

    if not deploy_folder.is_dir():
        raise ValueError(f"{actor_name}: no deploy folder found at '{deploy_folder}'")
    return deploy_folder

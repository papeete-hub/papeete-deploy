"""actor_source.py — locating an actor's own deploy folder: local convention, config file, env
vars, CLI args, and a real (local, no-network) git repo standing in for a remote source.
"""
import subprocess

import pytest
import yaml

from papeete_deploy import actor_source


def _init_git_repo(base, actors: dict) -> str:
    """A tiny local git repo with `<actor>/deploy/k8s/overlays/<recipe>` per entry in `actors`,
    committed on branch 'main' — a real git history `git clone` can fetch, fully offline."""
    repo = base / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    for actor, recipe in actors.items():
        overlay = repo / actor / "deploy" / "k8s" / "overlays" / recipe
        overlay.mkdir(parents=True)
        (overlay / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return str(repo)


# ── default local convention ─────────────────────────────────────────────────────────────────

def test_default_convention_resolves_a_sibling_folder_by_actor_name(tmp_path):
    product_path = tmp_path / "product.yaml"
    (tmp_path / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder == tmp_path / "customer" / "deploy"


def test_missing_sibling_folder_raises(tmp_path):
    product_path = tmp_path / "product.yaml"
    settings = actor_source.load_settings(product_path)
    with pytest.raises(ValueError):
        actor_source.resolve_actor_folder("customer", settings)


# ── config file ───────────────────────────────────────────────────────────────────────────────

def test_config_file_sets_the_global_local_root(tmp_path):
    product_path = tmp_path / "product.yaml"
    other_root = tmp_path / "elsewhere"
    (other_root / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    (tmp_path / "papeete-deploy.yaml").write_text(yaml.safe_dump(
        {"actorDeploySource": {"type": "local", "root": str(other_root)}}))

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder == other_root / "customer" / "deploy"


def test_config_file_sets_the_global_git_source(tmp_path):
    repo = _init_git_repo(tmp_path, {"customer": "develop"})
    product_path = tmp_path / "product" / "product.yaml"
    product_path.parent.mkdir()
    (product_path.parent / "papeete-deploy.yaml").write_text(yaml.safe_dump(
        {"actorDeploySource": {"type": "git", "url": repo, "ref": "main"}}))

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder.name == "deploy"
    assert (folder / "k8s" / "overlays" / "develop").is_dir()


def test_per_actor_local_override_wins_over_the_global_source(tmp_path):
    product_path = tmp_path / "product.yaml"
    (tmp_path / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    override_folder = tmp_path / "special-customer"
    (override_folder / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    (tmp_path / "papeete-deploy.yaml").write_text(yaml.safe_dump({
        "actorDeployOverrides": [
            {"actor": "customer", "type": "local", "path": str(override_folder)},
        ],
    }))

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder == override_folder / "deploy"


def test_per_actor_git_override_wins_over_the_global_source(tmp_path):
    repo = _init_git_repo(tmp_path, {"customer": "develop"})
    product_path = tmp_path / "product.yaml"
    (tmp_path / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    (tmp_path / "papeete-deploy.yaml").write_text(yaml.safe_dump({
        "actorDeployOverrides": [
            {"actor": "customer", "type": "git", "url": repo, "ref": "main"},
        ],
    }))

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    # resolved from the CLONE, not the local sibling folder that also exists
    assert folder != tmp_path / "customer" / "deploy"
    assert (folder / "k8s" / "overlays" / "develop").is_dir()


# ── precedence: CLI > env > config file ──────────────────────────────────────────────────────

def test_cli_arg_wins_over_env_var_and_config_file(tmp_path, monkeypatch):
    product_path = tmp_path / "product.yaml"
    cli_root = tmp_path / "from-cli"
    env_root = tmp_path / "from-env"
    (cli_root / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    (env_root / "customer" / "deploy").mkdir(parents=True)
    (tmp_path / "papeete-deploy.yaml").write_text(yaml.safe_dump(
        {"actorDeploySource": {"type": "git", "url": "https://example.invalid/should-not-be-used"}}))
    monkeypatch.setenv(actor_source.ENV_SOURCE, "local")
    monkeypatch.setenv(actor_source.ENV_ROOT, str(env_root))

    settings = actor_source.load_settings(product_path, cli_type="local", cli_root=str(cli_root))
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder == cli_root / "customer" / "deploy"


def test_env_var_wins_over_config_file(tmp_path, monkeypatch):
    product_path = tmp_path / "product.yaml"
    env_root = tmp_path / "from-env"
    (env_root / "customer" / "deploy" / "k8s" / "overlays" / "develop").mkdir(parents=True)
    (tmp_path / "papeete-deploy.yaml").write_text(yaml.safe_dump(
        {"actorDeploySource": {"type": "git", "url": "https://example.invalid/should-not-be-used"}}))
    monkeypatch.setenv(actor_source.ENV_SOURCE, "local")
    monkeypatch.setenv(actor_source.ENV_ROOT, str(env_root))

    settings = actor_source.load_settings(product_path)
    folder = actor_source.resolve_actor_folder("customer", settings)
    assert folder == env_root / "customer" / "deploy"


# ── errors ────────────────────────────────────────────────────────────────────────────────────

def test_git_source_with_no_url_anywhere_raises(tmp_path):
    product_path = tmp_path / "product.yaml"
    with pytest.raises(ValueError):
        actor_source.load_settings(product_path, cli_type="git")


# ── clone caching ─────────────────────────────────────────────────────────────────────────────

def test_two_actors_sharing_one_git_repo_clone_it_once(tmp_path):
    repo = _init_git_repo(tmp_path, {"customer": "develop", "waiter": "develop"})
    settings = actor_source.Settings(
        global_source=actor_source.Source(type="git", url=repo, ref="main"))

    clones: dict = {}
    actor_source.resolve_actor_folder("customer", settings, clones)
    actor_source.resolve_actor_folder("waiter", settings, clones)
    assert len(clones) == 1

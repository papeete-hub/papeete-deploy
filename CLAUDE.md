# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`papeete-deploy` resolves a [`papeete-product`](https://github.com/papeete-hub/papeete-product)'s
declared version queries (`label` + `version`/`featureName` — never a location, never a
pre-resolved tag) against a *real registry*, then deploys the result. It's the "touch reality"
half of the Papeete ecosystem's deployment story — `papeete-product` and `papeete-actor` define
identity/contracts and deliberately never know about a registry, a Docker daemon, or a k8s
cluster; this package is the one place that does. See `README.md` for the full picture and
`adr/` for the reasoning behind each major decision — read the relevant ADR before changing
resolution, k8s deployment, or actor-folder-location behavior; don't just re-derive it from code.

## Commands

```bash
uv run --extra dev pytest -q                # full suite (see markers below)
uv run --extra dev pytest -m "not e2e"      # fast, offline structural suite only
uv run --extra dev pytest -m e2e            # e2e only — needs a Docker daemon; k8s cases also need a kubeconfig context
uv run --extra dev pytest tests/test_k8s.py -k some_test   # a single test
uv build                                     # build the wheel (release.yml does this in CI)
```

There is no separate lint/typecheck command configured in this repo (no ruff/mypy config in
`pyproject.toml`) — don't assume one exists.

`tests/test_e2e_deploy.py` and `tests/test_e2e_k8s.py` self-skip when their prerequisite (a Docker
daemon; a kubeconfig context) isn't available, the same way the CLI's own error handling for a
missing daemon does — don't "fix" a skip by mocking Docker/kubectl.

Note: `papeete-product` has no PyPI release yet, so `pyproject.toml`'s `[tool.uv.sources]`
resolves it from a sibling checkout (`../papeete-product`) for local dev, and CI checks out both
repos as siblings for the same reason. If that sibling checkout is missing, dependency resolution
will fail.

## Architecture

**Pipeline: resolve → deploy/undeploy, dispatched on `environment.type`.**

- `registry.py` — `Registry` protocol: `list_tags(name) -> list[str]` (newest first) and
  `image_name(name) -> str | None` (the registry-qualified repository to pull from, or None when
  images need no qualifying — `ADR-PD-0006`). `LocalDockerRegistry` (the local Docker daemon's
  image store) is fully tested. `AcrRegistry`'s `image_name()` is tested offline; its
  `list_tags()` (shells to `az acr repository show-tags`) is **still not wired into CI and not
  exercised by any test** — it needs a live registry. Don't treat that half as verified.
- `deploy.py` — the core module. `resolve_one()`/`resolve_versions()` fold each actor's declared
  query against a `Registry`'s tag list (reusing `papeete_version.npm_range` for range
  satisfaction) — resolution is **registry-based, never git-based** (a deliberate choice, see
  `ADR-PD-0001`); no match ever falls back silently, it's a hard `ValueError`. `deploy()`/
  `undeploy()` dispatch on `product.yaml`'s `environment.type`: `"local"` drives Docker Compose
  (`up`/`down`/`port`/`compose`, one shared project, actors reachable by name via Compose's
  embedded DNS); `"k8s"` hands off to `k8s.py`.
- `k8s.py` — applies each actor's own `deploy/k8s/overlays/<recipe>` (kustomize, actor-authored
  per `papeete-actor`'s `ADR-PA-0025`) with the resolved image tag injected. **Never edits or
  copies the actor's own files** — wraps the chosen overlay in a fresh temporary kustomization
  (`images: [...]` overriding the tag, `commonLabels` stamping `managed-by`/`product`) and renders
  via `kubectl kustomize --load-restrictor=LoadRestrictionsNone` (not `apply -k` directly — its
  loader rejects the wrapper's cross-directory reference). `ensure_namespace()` creates but never
  deletes; `delete()` tears down only labeled resources (`all,configmap,ingress`), never the
  namespace.
- `actor_source.py` — locates each k8s-targeted actor's own **deploy folder** (three tiers, most
  to least specific): a per-actor override (config-file only, `papeete-deploy.yaml`'s
  `actorDeployOverrides`), a global source (CLI flag > env var > config file's
  `actorDeploySource` > zero-config convention: a sibling folder of `product.yaml` named exactly
  the actor's declared `name`). Every resolution path normalizes to the actor's own `.../deploy`
  folder so `k8s._overlay_dir()` never has to special-case local vs. git. `deploy()`'s k8s branch
  validates every actor's folder+overlay exists *before* applying any of them (two-pass:
  validate-then-apply) — a missing overlay must fail loudly with nothing partially deployed.
- `cli.py` — thin argparse wrapper: `resolve` (print resolved tags, no Docker/kubectl touched),
  `deploy`, `undeploy`. `(FileNotFoundError, ValueError)` at the top level are user-fixable
  mistakes and print `FAIL <message>` without a traceback — everything else is a real bug and
  should surface as one; don't broaden that except clause.
- `report.py` — the `ok`/`note`/`warn`/`FAIL` `Report` style, **deliberately duplicated** from
  `papeete-product` rather than imported — this package pays a few duplicated lines to stay a
  standalone package with no extra runtime dependency for one small dataclass. Don't "fix" this
  duplication by importing it.

**Cross-repo contracts this package depends on but doesn't own**: `product.yaml`'s schema
(`papeete-product`, particularly the typed `environment: {type, name, k8sName}` mapping and
per-actor `recipe`, `ADR-PP-0003`) and an actor's `deploy/k8s/base+overlays` kustomize layout
(`papeete-actor`'s `ADR-PA-0025`, base Deployment's image name must equal the actor's own
normalized name with no tag — that's the hook this package injects the resolved tag into). Changes
to either contract likely need a corresponding ADR here, not just a code change.

**`examples/`** is a complete, runnable, self-contained product (`customer` + `waiter` actors,
each with `deploy/k8s/`) that the test suite and README's worked example both exercise directly —
treat it as fixture data that tests and docs depend on, not disposable sample content.

## Releasing

Tag-triggered (`git tag vX.Y.Z && git push origin vX.Y.Z`) via PyPI Trusted Publishing (OIDC) —
`.github/workflows/release.yml`. No token stored anywhere. The workflow does **not** install the
built wheel into a clean environment first to verify it (unlike `papeete-actor`'s release
workflow) — this is a known, open gap (see README's "Releasing" section), not an oversight to fix
incidentally.

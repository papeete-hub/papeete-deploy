---
id: ADR-PD-0001
title: "papeete-deploy is a standalone package, resolving product queries against a registry"
status: Accepted
date: 2026-08-21
supersedes: []
references:
  - src/papeete_deploy/registry.py
  - src/papeete_deploy/deploy.py
  - examples/product.yaml
---

# ADR-PD-0001 — papeete-deploy is a standalone package, resolving product queries against a registry

## Context

`papeete-product`'s `product.yaml` names each actor by identity and a **query** — `label` (a
ciType) + `version` (`"latest"`, a short SHA, or an npm-range), `papeete-version`'s own vocabulary
(`papeete-product`'s `ADR-PP-0002`). Turning that query into a real, runnable tag needs to consult
something real. Docker Compose orchestration (`up`/`down`/`port`/`compose`) also needs to touch a
real Docker daemon. Both are "location" work — exactly what `papeete-product` refuses to know,
by design, since `ADR-PP-0001`.

The first instinct for resolution was `papeete-version match_version()`, which folds a query
against **git** state in an actor's own folder. That would have required `papeete-product`'s
products to carry a folder somewhere after all — a location, contradicting the whole point. A
real, external prior art for this exact problem — resolving a deploy-time query into a concrete
version — was reviewed while designing this: an existing production deployment pipeline, which
resolves `"latest"` and friends by **listing tags a registry already has** (via its cloud registry
and artifact-repository APIs), filtering by ciType/branch, and taking the newest match — never
touching git. That settles the resolution question differently, and better: no folder needed,
only registry access, which deploying anything needs regardless.

That same pipeline also makes clear this is fundamentally a **deployment** concern, not a generic
"runtime" one: resolving a query and then making a product real happens *somewhere specific* — an
environment, with its own registry, eventually its own execution target (a k8s cluster,
Terraform-provisioned infra, a DB-migration step) — not just "start some containers." This
package is named and framed after that concept from the start, even though today it only
implements the narrowest slice of it.

## Decision

**`papeete-deploy` is a new, separate, standalone Python package** — its own repo
(`papeete-hub/papeete-deploy`, sibling to `papeete-product`), its own `pyproject.toml`, its own
CLI, its own decision log.

- **Resolution is registry-based, not git-based.** `registry.py` defines a small `Registry`
  protocol (`list_tags(name) -> list[str]`) with two implementations: `LocalDockerRegistry`
  (queries the local Docker daemon's own image store — fully implemented and tested) and
  `AcrRegistry` (shells to `az acr repository show-tags` — sketched to the same protocol, **not
  wired into the CLI's default path and not covered by any test**, since this environment has no
  ACR access to verify against). `deploy.resolve_one()` folds a `{label, version, featureName}`
  query against whatever tags a given `Registry` reports, reusing `papeete_version.npm_range` for
  range satisfaction and duplicating the tiny label/semver-core string-splitting helpers locally
  (the same "duplicate a few lines rather than take on a dependency" norm `papeete-product`'s
  `ADR-PP-0001` already established) — **no dependency on `papeete-version`'s git-based
  `compute()`/`match_version()`, and no actor folder needed anywhere in this package.**
- **Docker Compose orchestration moves here from `papeete-product`**: `normalize()`,
  `image_tag()`, `_docker_reachable()`, `_image_exists()`, `compose()`, `up()`, `down()`,
  `port()`, `project_name()` — moved essentially unchanged, adapted so `compose()`/`up()` consume
  an already-resolved actor list (`resolve_versions()`'s output) rather than a product's raw
  declared query.
- **The `environment` field `product.yaml` now requires is read, but not yet acted on.** The CLI
  takes an explicit `--registry {local,acr}` (default `local`) and `--acr-name`; mapping a
  declared environment name to a registry automatically is a deliberate, flagged follow-up (see
  Consequences), not designed speculatively before the second backend is real.

## Rationale

**A contract's package should match its altitude** — the same principle `papeete-product`'s
`ADR-PP-0001` already applied one level up. Declaring a query is identity-adjacent; folding that
query against a live registry, and then starting containers, needs to touch a registry and a
Docker daemon — reality this ecosystem's identity contracts (`papeete-product`, `papeete-actor`)
have no business knowing about.

**Registry-based resolution over git-based**, because the actual, load-bearing question a
deployment asks — "what can I run right now?" — is answered by what a registry actually has, not
by what git *would* produce if built. A registry's tag list is the ground truth of what's
deployable; git state is one step removed from that (an actor could be tagged, git-taggable, and
still never actually pushed anywhere).

**Deployment, not runtime, as the frame** — because the concept this package exists to serve
(resolve a query, then make a product real somewhere specific, with its own registry and
eventual execution target) is exactly what "deployment" already names in this ecosystem's own
prior art, and naming it accurately now avoids a second rename later once k8s/Terraform/multiple
environments actually arrive.

## Consequences

- **New dependency graph**: `papeete-deploy` → `papeete-product` (schema + `resolve()`) +
  `papeete-version` (`npm_range` only — not the git-based `compute()`/`match_version()`) + Docker.
- **`papeete-product` has no PyPI release yet**, so `pyproject.toml`'s `[tool.uv.sources]`
  resolves it from a sibling checkout (`../papeete-product`) for local dev; CI checks out both
  repos as siblings for the same reason. Both should be removed once `papeete-product` publishes.
- **Open — `AcrRegistry` is unverified.** The right shape, not a tested implementation. Wiring and
  testing it against a real ACR is explicit future work, not done here.
- **Open — no `environment` → registry auto-mapping yet.** `environment` is required and read, but
  the CLI still takes an explicit `--registry` override. A real mapping (likely a small
  `papeete-deploy`-owned config file: `environment name -> {type: local}` or `{type: acr, name:
  ...}`) is the natural next step once the ACR backend is real, not designed speculatively now.
- **Open — the worked example's actors have no git tag yet.** `examples/customer` and
  `examples/waiter` have no `customer/v0.1.0`-style tag, so a real `papeete-actor build` can't
  produce a resolvable version for them today; the worked example's `built` fixture stands in
  with a plain `docker build`, tagged by hand in the real shape, the same way `papeete-product`'s
  now-deleted `test_e2e_product.py` always stood in for `papeete-actor build`.
- **Open — multiple environments, k8s, Terraform, DB migrations.** All Isagri-shaped territory
  this package is named and framed for, none of it implemented. Not designed speculatively here;
  each is its own future ADR once it's actually needed.

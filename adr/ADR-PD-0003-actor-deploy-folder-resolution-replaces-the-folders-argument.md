---
id: ADR-PD-0003
title: "Actor deploy-folder resolution (local convention, git source, per-actor overrides) replaces the folders CLI argument"
status: Accepted
date: 2026-08-22
supersedes: []
references:
  - src/papeete_deploy/actor_source.py
  - src/papeete_deploy/k8s.py
  - src/papeete_deploy/deploy.py
  - src/papeete_deploy/cli.py
---

# ADR-PD-0003 — Actor deploy-folder resolution (local convention, git source, per-actor overrides) replaces the folders CLI argument

## Context

`ADR-PD-0002` shipped k8s deploys with every actor's own folder passed explicitly, positionally,
on every `papeete-deploy deploy` invocation — matched to an actor by reading that folder's own
`actor.yaml`. In practice that's pure friction: forgetting a folder fails with `FAIL customer: no
folder given for a k8s-targeted actor`, and nothing about the invocation hints at what to pass.

Yoann asked for this to be configurable instead, along three tiers of precedence (most to least
specific): a per-actor override, a global source (a local-folder convention, or a git repo to
fetch from), and a zero-config default. Clarified via follow-up: settings may come from a config
file, env vars, and CLI flags together (CLI > env > file, for the *global* source only — a
per-actor override is structured data a flat env var or flag doesn't fit, so it lives in the
config file alone); the `folders` positional is removed entirely, not kept as a fallback;
local-convention matching is by folder name equalling the actor's own declared name, not by
reading `actor.yaml` (which drops the need to read it at all); and the git source is one shared
repo URL with a fixed per-actor subpath convention.

## Decision

**New module, `actor_source.py`.** `Settings` holds one `global_source` (`Source`: `type` `local`
or `git`, plus `root`/`url`/`ref`) and a `dict[actor_name, Source]` of `overrides`.
`load_settings(product_path, cli_type=..., cli_root=..., cli_git_url=..., cli_git_ref=...)` reads
`<product_path's dir>/papeete-deploy.yaml` (`actorDeploySource` + `actorDeployOverrides`, both
optional), layers `PAPEETE_DEPLOY_ACTOR_SOURCE`/`_ROOT`/`_GIT_URL`/`_GIT_REF` over it, then the
`cli_*` args over that — CLI wins, then env, then the config file, then a local convention whose
`root` defaults to `product_path`'s own parent directory. `resolve_actor_folder(actor_name,
settings, clones)` checks `overrides[actor_name]` first, else `global_source`; `git` sources clone
(`git clone --depth 1 [--branch ref]`) into a tempdir, cached in `clones` (keyed by `(url, ref)`,
one dict per `deploy()` call) so actors sharing one repo+ref clone it once, not once each.

**Resolution always returns an actor's own DEPLOY folder, not its root.** Local: `<root>/
<actor-name>/deploy` (global/convention) or `<override.path>/deploy` (per-actor override, already
actor-specific). Git: `<clone>/<actor-name or override.subpath>/deploy`. `k8s._overlay_dir()`
shrinks to match — it now looks for `k8s/overlays/<recipe>` directly under what it's given,
instead of joining a `/deploy` segment itself. This makes local and git resolution structurally
identical and needs no changes to today's on-disk example layout
(`examples/customer/deploy/k8s/...`) — only to the code that locates it.

**`k8s.py`'s `_actor_name()`/`locations_from_folders()` are deleted** — dead code once matching is
by folder name, not by reading `actor.yaml`.

**CLI**: the `deploy` subcommand's `folders` positional is gone. Four new optional flags —
`--actor-source {local,git}`, `--actor-root PATH`, `--actor-git-url URL`, `--actor-git-ref REF` —
feed `load_settings()`'s `cli_*` kwargs. `deploy.deploy()` gains an `actor_source: Settings | None`
parameter (defaults to `load_settings(product_path)` with no CLI/env args if omitted, so a library
caller that doesn't care about that layering can just call `deploy(path, registry)`).

## Rationale

**Per-actor overrides are config-file-only, deliberately** — a list of `{actor, type, ...}`
mappings has no clean single-flag or single-env-var shape, and forcing one (`--actor-override
name=git:url@ref`, repeatable) would be more surface for one tier than the other two combined, for
a case (an individual actor needing a different source than everyone else) that's inherently rare
and worth writing down rather than typing on every invocation.

**The deploy-folder normalization (not actor-root) makes local and git resolution the same
shape.** Without it, the git convention (`<repo>/<actor-name>/deploy`, chosen because it reads
naturally as "here's the deploy folder") and the local convention (`<root>/<actor-name>`, an actor
root) would disagree about what "resolve an actor" returns, forcing `_overlay_dir()` or its caller
to special-case one of them. Normalizing once, in `actor_source.py`, keeps every caller downstream
of resolution — `k8s._overlay_dir()`, `k8s.apply()` — ignorant of which source produced the path.

**Clone-and-cache, not sparse-checkout**, for the git source: deploy-config repos are expected to
be small (kustomize YAML, not application source), so a full shallow clone per unique `(url, ref)`
is the simplest correct thing, and the cache means a product with several actors on one shared
repo still only clones once per `deploy()` call.

## Consequences

- **Breaking CLI change.** The `folders` positional no longer exists; any script passing folders
  positionally must switch to a config file, env vars, or the new `--actor-*` flags, or rely on
  the zero-config convention if its layout already matches (a sibling folder per actor, named
  after it — which is exactly `examples/`'s own layout, so this repo's worked example needs no
  flags for `productK8s.yaml` deploys now).
- **New optional file shape: `papeete-deploy.yaml`**, checked in next to `product.yaml`. Absent
  entirely for a product that only uses the default convention or CLI/env overrides.
- **`git` becomes a soft runtime dependency** — only exercised on the k8s-with-a-git-source path;
  `local`/Compose deploys and the zero-config k8s convention need it not at all.
- **Does not supersede `ADR-PD-0002`** — its wrapper-kustomization image injection, label-based
  non-destructive teardown, and validate-then-apply-across-actors decisions are unchanged; this
  ADR only changes how an actor's own folder is located before those decisions apply.
- **Open — no CI coverage for the git-source path against a real remote.** `test_actor_source.py`
  exercises `git clone` for real, but only against a local, offline repo created for the test —
  auth (SSH keys, tokens) for a private remote is unexplored.

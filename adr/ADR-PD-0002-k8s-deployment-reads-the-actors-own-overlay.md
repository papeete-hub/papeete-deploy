---
id: ADR-PD-0002
title: "k8s deployment reads the actor's own kustomize overlay; deploy/undeploy replace run/stop"
status: Accepted
date: 2026-08-22
supersedes: []
references:
  - src/papeete_deploy/k8s.py
  - src/papeete_deploy/deploy.py
  - src/papeete_deploy/cli.py
  - examples/product.yaml
---

# ADR-PD-0002 — k8s deployment reads the actor's own kustomize overlay; deploy/undeploy replace run/stop

## Context

`ADR-PD-0001` shipped the narrowest slice of this package: registry-based resolution plus local
Docker Compose orchestration, with k8s explicitly named as territory this package is framed for
but not yet built. A real, production-shaped k8s deploy needs more than this package can
synthesize on the fly from an actor's `name` and resolved tag — resource shape, probes, and
per-environment overlays are all things a real Deployment/Service pair needs, so the natural owner
of that shape is the actor's own folder, the same way `Dockerfile` already is
(`papeete-actor`'s `ADR-PA-0025`). Reference: an internal deploy folder's `base`+`overlays`
kustomize shape, inspected read-only via `glab api` for its folder layout only — none of its
content copied.

Landing this also required folding in two prerequisites this ADR's design depends on directly:
`product.yaml`'s `environment` becoming a typed `{type, name, k8sName}` mapping instead of a free
string, and a per-actor `recipe` field (`papeete-product`'s `ADR-PP-0003`) — both needed so a
`deploy()` call knows *which kind* of target, *which* cluster+namespace, and *which* overlay per
actor, none of which a bare `environment` string could carry.

## Decision

**The CLI's `run`/`stop` become `deploy`/`undeploy`**, dispatching on `product["environment"]
["type"]` rather than assuming Compose. `local` behaves exactly as `run`/`stop` always did
(delegates to the unchanged `up()`/`down()`). `k8s` is new:

- **`k8s.py`** wraps an actor's chosen overlay (`<folder>/deploy/k8s/overlays/<recipe>`) in a
  fresh temporary kustomization — `resources` pointing at the overlay (a path relative to the
  wrapper's own tempdir; kustomize's root-reference check rejects an absolute one outright,
  regardless of load-restrictor settings), `images` overriding the base image's tag to the
  resolved version, `commonLabels` stamping `app.kubernetes.io/managed-by: papeete-deploy` and
  `papeete-deploy/product: <product name>`. The actor's own files are never copied or edited —
  same discipline `deploy.py`'s `_compose_file()` already uses for Compose.
- **Rendered via `kubectl kustomize ... --load-restrictor=LoadRestrictionsNone`, then `kubectl
  apply -f -`** — not `kubectl apply -k` directly. `apply -k`'s built-in loader enforces
  `LoadRestrictionsRootOnly` with no flag to relax it; rendering first and applying the plain
  manifest sidesteps that while keeping the actor's overlay physically untouched and outside the
  wrapper's root.
- **`ensure_namespace()` creates the target namespace if missing, never deletes it.** `delete()`
  tears down only what it (by the `papeete-deploy/product` label) knows this package created —
  `all,configmap,ingress` — never the namespace itself.
- **`deploy()`'s k8s branch validates every actor's folder and overlay exist *before* applying
  any of them** (two-pass: validate-then-apply) — a missing overlay for actor 2 must not leave
  actor 1 already applied. `folders` (an actor's own folder, needed only to locate its overlay) is
  a new, optional CLI positional (`nargs="*"`) — required per k8s-targeted actor only, enforced by
  `deploy()`'s own `ValueError`, not argparse.

**`environment` → `--registry` auto-mapping stays explicitly out of scope**, per `ADR-PD-0001` —
`--registry`/`--acr-name` remain manual CLI flags, untouched by this change.

## Rationale

**Folders are legitimately back in scope for k8s, consistent with `ADR-PD-0001`, not a
contradiction of it.** `ADR-PP-0001`/`ADR-PP-0002`'s line was that `papeete-product` never knows
location — this package has always been the one allowed to touch reality (a registry, a Docker
daemon) to make a product real *somewhere specific*. An actor's folder, passed explicitly as a CLI
argument rather than discovered, is exactly that kind of location knowledge — the same class this
package already has for Compose (an actor's own `Dockerfile`/image, resolved by name).

**Render-then-apply over `apply -k` directly** was the practical outcome of actually running this
against a live cluster (Docker Desktop's Kubernetes) while implementing it: `apply -k`'s loader
rejected the wrapper's cross-directory `resources` reference outright, with no equivalent flag on
`apply` itself to relax it the way `kubectl kustomize` exposes. Two steps against one plain
manifest is no less correct, and keeps the actor's overlay exactly where the actor author put it.

**Validate-then-apply, not apply-as-you-go**, follows the same "fail loudly, no partial silent
state" discipline this ecosystem already applies elsewhere (`ADR-PA-0022`'s no-fallback-version,
this package's own hard `ValueError` on an unresolvable query). A multi-actor deploy that's half
applied because actor 3 of 4 was missing an overlay is a worse failure mode than refusing to start
at all.

## Consequences

- **Breaking CLI change.** `papeete-deploy run`/`stop` no longer exist; any script invoking them
  must switch to `deploy`/`undeploy`. Nothing has shipped depending on the old verbs.
- **New dependency surface: `kubectl` on `PATH`, and a working kubeconfig context** for the k8s
  path — `local`/Compose is entirely unaffected and needs neither.
- **`examples/customer`/`examples/waiter` gain `deploy/k8s/` folders** (kustomize `base` +
  `overlays/develop`, duplicated from `papeete-product`'s own copies per the existing
  self-contained-examples norm) and `examples/product.yaml`'s `environment` is now the typed
  mapping (`ADR-PP-0003`).
- **`tests/test_e2e_k8s.py` needs a real cluster to run** (self-skips via `kubectl config
  current-context`, mirroring how Compose e2e self-skips with no Docker daemon) — verified against
  this machine's `docker-desktop` context; not exercised in CI, where no cluster exists.
- **No rollback across a multi-actor deploy beyond validate-then-apply's ordering.** Once
  validation passes and `apply()` calls begin, a later actor's `kubectl apply` failure does not
  undo an earlier actor already applied in the same call — validate-then-apply only prevents
  starting on a plan already known to be incomplete, not a failure mid-application.
- **Terraform folder (`deploy/terraform/`, `ADR-PA-0025`) is convention-only** — nothing here
  executes it.
- **Open — `environment` → `--registry` auto-mapping.** Still undecided, unchanged from
  `ADR-PD-0001`.
- **Open — local-image visibility on other clusters.** Verified only against Docker Desktop's
  Kubernetes, whose node shares the host's Docker image store. A cluster with its own separate
  image store needs those images pushed somewhere it can pull from first — not solved here.

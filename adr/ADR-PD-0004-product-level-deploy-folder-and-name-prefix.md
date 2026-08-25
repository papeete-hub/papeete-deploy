---
id: ADR-PD-0004
title: "Product-level k8s deploy folder, and a product-scoped namePrefix for every k8s wrapper"
status: Accepted
date: 2026-08-25
supersedes: []
references:
  - src/papeete_deploy/k8s.py
  - src/papeete_deploy/deploy.py
  - examples/deploy/k8s/
  - examples/productK8s.yaml
  - examples/customer/deploy/k8s/base/deployment.yaml
---

# ADR-PD-0004 — Product-level k8s deploy folder, and a product-scoped namePrefix for every k8s wrapper

## Context

`deploy()`'s k8s branch (`ADR-PD-0002`) only ever loops over a product's `actors:` and applies
each one's own kustomize overlay — there is no home for a resource that belongs to the *product*
as a whole. The motivating case: a consumer product ("table-service", actors `customer`+`waiter`)
wants a Grafana dashboard ConfigMap describing both actors together, with a template-variable
drill-down between them. Bolting that onto one actor's lifecycle means deleting that actor also
deletes the whole product's dashboard; duplicating it into every actor's own overlay duplicates
content — the same shape of duplication a consumer repo's own ADR already tore out once, for a
different reason (a shared `registry.yaml`).

Investigation confirmed `product.yaml`'s schema has no product-level-extras field and none of
`ADR-PD-0001`–`0003` considered and rejected this — it's new territory.

A second, related gap surfaced while designing this: an actor's own base overlay names its
objects with its own bare normalized name (e.g. a `Service` named `customer`), with zero
product-level scoping. Compose (`environment.type: local`) already gets this for free —
`container_name: f"{project}-{name}"` plus `docker compose -p project` — but the k8s path has no
equivalent, so two products sharing a namespace/cluster, or an external, non-namespaced shared
resource (e.g. RabbitMQ), can collide on unprefixed names today.

## Decision

**Product-level deploy folder**: a sibling `deploy/` directory next to `product.yaml` itself
(e.g. `examples/deploy/k8s/...`), directly analogous to `actor_source.py`'s own zero-config
convention (`<product.yaml's parent>/<actor-name>/deploy`) but for the product as a whole —
`<product.yaml's parent>/deploy`. **No override tiers** (no CLI/env/config-file layering) in this
pass, deliberately: actor-folder resolution itself started as a fixed convention (`ADR-PD-0002`)
before `ADR-PD-0003` added layering later, once real friction justified it. Same path here if it's
ever needed.

**Recipe selection**: a new optional `recipe` key on `product.yaml`'s existing `environment`
mapping (`environment: {type, name, k8sName, recipe}`) — distinct from each actor's own per-actor
`recipe`; this one selects the product-level deploy folder's own overlay
(`<product-deploy-folder>/k8s/overlays/<environment.recipe>`). Its *presence* is the opt-in
trigger: absent, product-level deploy is skipped entirely; present, the folder+overlay must exist
— validated in the same pre-flight pass `deploy()` already runs for actors (`ADR-PD-0002`'s
validate-then-apply), so a missing product-level overlay fails loudly before any actor is applied
either.

**Apply mechanism**: `k8s.apply_product()` reuses the existing wrap-never-edit discipline
(`_wrapper_kustomization()`/render/`ensure_namespace()`) with no image/tag to inject — the
`images` key becomes conditional, omitted when there's no image. `delete()` needs no change: it
already sweeps everything labeled `papeete-deploy/product=<name>` across `all,configmap,ingress`,
and product-level resources carry that same label via the same wrapper path.

**Product-scoped naming**: add `namePrefix: f"{normalize_name(product_name)}-"` to *every* k8s
wrapper kustomization — every actor's, and the product-level one, no exceptions. `commonLabels`'s
`papeete-deploy/product` value stays raw/unnormalized (unchanged) — only the new `namePrefix` uses
the normalized form.

**Explicitly not done**: no env-var-injection mechanism for cross-actor hostname discovery.
Kubernetes DNS resolution is already namespace-scoped — whatever an object's real (now-prefixed)
name is, is simply the name a caller must use; `namePrefix` needs no compensating mechanism to
"enable" that, k8s already does it structurally. `namePrefix` also already reaches cluster-scoped
kinds (`ClusterRole`, CRDs, …) uniformly — no separate handling needed there.

## Rationale

The product-level folder is the minimal-surface analogue of the actor convention already in
place — no new resolution machinery, no new dependency. `environment.recipe` (not a top-level
product field) because it selects the product's *own* overlay the same way each actor's `recipe`
selects that actor's own overlay — structurally the same kind of field, one level up.

`namePrefix` (not manual per-manifest renaming, not a new naming DSL) because kustomize's
built-in `nameReference` transformer already fixes up in-overlay references for free — verified
live: rendering the `customer` example overlay through a wrapper with `namePrefix: table-service-`
correctly rewrote the Ingress's `backend.service.name` from `customer` to `table-service-customer`
with no extra work.

No env-var-injection for discovery, because it would solve a problem that doesn't exist: an
object's name is an object's name, and k8s's own DNS is namespace-relative regardless of what
that name is. The genuine remaining gap — a literal string value `namePrefix` cannot see (a
ConfigMap `data` entry, an env var value, an actor's own hardcoded application string) — is not a
k8s-native reference at all, so no structural mechanism here could rewrite it safely; it's the
deploy-folder/actor author's own responsibility to make such values product-specific if needed.

## Consequences

- **Breaking rename on redeploy.** Every actor's k8s objects rename (`customer` →
  `table-service-customer`); anything watching/scripting against the old bare names must update.
- `examples/customer/deploy/k8s/overlays/develop/ingress.yaml`'s URL path routing is unaffected by
  `namePrefix` itself — a URL path is not an object name; only `metadata.name`/name-references
  change, and those are auto-fixed by kustomize. **But this turned out to matter in practice**:
  ingress-nginx's admission webhook enforces host+path uniqueness *cluster-wide*, and this
  example's original path (`/develop/customer/api...`) collided for real with an already-deployed
  "table-service" product on the same cluster (deployed before `namePrefix` existed, so still
  bare-named) — a live instance of the exact "resource not namespace-related" gap this ADR already
  flags. Fixed the same way as the `WAITER_URL` finding below: hand-prefixed to
  `/table-service/develop/customer/api...` (and the `waiter` example's own path likewise) —
  authored by the deploy-folder author, not injected by `papeete-deploy`, since a URL path is
  exactly the kind of literal value `namePrefix` structurally cannot reach.
- **Found and fixed during this work**: `examples/customer/app.py` hardcoded
  `WAITER_URL = "http://waiter:8080/"` — a literal string in the actor's own application source,
  invisible to `namePrefix`. Now `os.environ.get("WAITER_URL", "http://waiter:8080/")`, with the
  k8s-specific value (`http://table-service-waiter:8080/`) supplied as a static env var in
  `examples/customer/deploy/k8s/base/deployment.yaml` — hand-authored by the deploy-folder author,
  same as any other Deployment env var, not something `papeete-deploy` injects. Compose is
  unaffected (its Service key stays bare `waiter`, no product prefix there; the env var is simply
  unset, so the code falls back to today's value).
- Product-level apply happens before the per-actor loop in `deploy()`'s k8s branch, but this
  ordering is arbitrary — no real dependency between a product-level ConfigMap and any actor's
  Deployment.
- `test_e2e_k8s.py`'s product name changes from `table-service-e2ek8s` to `table-service` to match
  the now-hardcoded `WAITER_URL` value; `environment.name` (already a random per-run namespace)
  continues to provide test-run isolation, unaffected by this rename.
- **Open** — no override tiers for the product-level folder (no git source, no per-invocation
  override). If a consumer product needs that, it's the same kind of follow-up `ADR-PD-0003` was
  for actors: a separate ADR once real friction shows up, not designed speculatively here.
- **Open** — external, non-namespaced shared resources (RabbitMQ queues, external DB schemas,
  etc.) still need the deploy-folder author to embed product context into those values by hand;
  `papeete-deploy` has no visibility into them and will not attempt to rewrite them.

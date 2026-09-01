---
id: ADR-PD-0007
title: "Deleting the namespace is an opt-in of undeploy, never its default"
status: Accepted
date: 2026-09-01
supersedes: []
references:
  - src/papeete_deploy/k8s.py
  - src/papeete_deploy/deploy.py
  - src/papeete_deploy/cli.py
  - adr/ADR-PD-0002-k8s-deployment-reads-the-actors-own-overlay.md
---

# ADR-PD-0007 — Namespace deletion is opt-in on undeploy

## Context

`ADR-PD-0002` gave this package a deliberate asymmetry: `ensure_namespace()` creates a namespace
if missing, and nothing ever removes one. `delete()` sweeps by label, tearing down only what this
package itself applied. That was right for the case it was written against — one long-lived
namespace per environment, deployed to repeatedly, holding an operator's own Secrets and whatever
else the environment needs.

It is wrong for the case that now exists. A product can be stood up N times, once per PR or per
task, each instance in its own namespace whose entire purpose is to be thrown away. Under the
label sweep alone, every one of those leaves an empty namespace behind for ever. They are not
free: they accumulate in listings, hold quota objects, and make `kubectl get ns` progressively
less readable.

Making deletion the new default would be worse than the problem. A namespace routinely holds
resources this package never created, and cannot distinguish them from its own — an undeploy of
one product from a shared namespace would take another product's Secrets with it.

## Decision

**`undeploy()` gains `delete_namespace: bool = False`, surfaced as `--delete-namespace`.** When
set, the label sweep runs first as it always has, and then `k8s.delete_namespace()` removes the
namespace and everything left in it. When unset — the default, and every existing caller —
behaviour is byte-identical to before.

`delete_namespace()` is idempotent (`--ignore-not-found`), so a repeated undeploy still succeeds.

## Rationale

**Only the caller knows which kind of namespace it is looking at.** Nothing in `product.yaml`
distinguishes an ephemeral instance from a long-lived one: `environment.name` is just a string,
and inferring ephemerality from a naming convention would be a guess this package has no business
making. So the caller says.

**Opt-in fails safe.** Forgetting the flag leaves a namespace behind — visible, harmless,
fixable. Defaulting to deletion and forgetting to opt *out* destroys resources with no undo. The
asymmetry in cost decides the default.

**The label sweep still runs first**, rather than being skipped as redundant. It keeps one
teardown path rather than two, and it means a caller watching for its own resources to disappear
sees the same events either way.

## Consequences

- **`ADR-PD-0002`'s "never deletes a namespace" is narrowed, not overturned.** It remains true of
  every default path; the exception is explicit, named, and reachable only on request.
- **The flag is destructive and says so** in its own help text: it removes resources
  `papeete-deploy` never created.
- **An ephemeral-instance workflow now cleans up after itself** — `papeete-deploy undeploy
  product.yaml --delete-namespace`, or a caller passing `delete_namespace=True` directly, as
  `BNK.RLVR.CAP.SUP.002.BEN-task-orchestration` does for the `test-<task_id>` namespace it creates
  per attempt.
- **Nothing infers ephemerality.** If a future `papeete-product` contract ever declares it, this
  decision can read it instead of asking — but it does not today, and this ADR does not add it.

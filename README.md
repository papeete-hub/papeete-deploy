# papeete-deploy

Resolves a [`papeete-product`](https://github.com/papeete-hub/papeete-product)'s declared version
queries against a real registry, and deploys the result. Split out of `papeete-product` because
resolving a query and orchestrating containers both need to touch reality (a registry, a Docker
daemon) in ways a product's own identity contract deliberately refuses — see
[ADR-PD-0001](./adr/ADR-PD-0001-papeete-deploy-is-a-standalone-package.md) and `papeete-product`'s
own `ADR-PP-0002`.

```
papeete-deploy resolve   PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
papeete-deploy run       PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
papeete-deploy stop      PRODUCT.YAML
```

`resolve` prints each actor's resolved tag, no Docker involved — the smaller claim, useful in CI
or for a human to check where a product would land before spending the time to run it. `run`
resolves the same way, then starts every actor via Docker Compose.

```bash
pip install papeete-deploy
```

## Framed as deployment, scoped narrowly for now

A product's declared query — `label` (a ciType) + `version` (`"latest"`, a short SHA, or an
npm-range) — has to be folded against *something real* to mean anything. This package resolves it
by listing the tags a **registry** already has for an actor's name, filtering by label/branch, and
taking the newest match — never by consulting git. That mirrors how a real, production deployment
pipeline solves the exact same problem (reviewed while designing this), and it's *why* this
package needs neither an actor's folder nor a `papeete-version` dependency on git: only registry
access, which deploying anything needs anyway.

That's also why this repo is named and framed as **deployment**, not "runtime" — resolving a query
against a registry and making a product real *somewhere specific* (an environment, its own
registry, eventually its own target: local Docker today, a k8s cluster or Terraform-provisioned
infra later) is a deployment concern. Only the narrowest slice of that is built right now: a
local-Docker registry backend and local Docker Compose orchestration. Multiple environments, an
Azure Container Registry backend, k8s, Terraform, DB-migration jobs — real territory this package
is meant to grow toward, explicitly not implemented yet (see `ADR-PD-0001`'s consequences).

## Registries

```python
from papeete_deploy.registry import LocalDockerRegistry, AcrRegistry
```

- **`LocalDockerRegistry`** — every tag the local Docker daemon already has for a name. Fully
  implemented and tested; the default (`--registry local`).
- **`AcrRegistry`** — every tag an Azure Container Registry has, via `az acr repository
  show-tags`. Sketched to the same protocol, **not wired into CI, not exercised by any test** — no
  ACR access from this environment to verify against. `--registry acr --acr-name NAME` selects it.

Which registry an actor's declared `environment` (required on every `product.yaml`) should map to
is **not yet automatic** — `--registry`/`--acr-name` are explicit CLI flags for now. See
`ADR-PD-0001`.

## Discovery is Docker's, not invented here

`papeete-deploy run` starts every actor on one shared Docker Compose project, each container named
after its `name` (normalized). Compose's own embedded network DNS resolves that name for every
other actor on the same project — no registry lookup, no sidecar, nothing new built for it. Every
actor's server is expected to listen on a fixed port, `8080`, published to a host-assigned
ephemeral port so a caller outside Docker can reach it too.

## The worked example

[`examples/`](./examples/) is a complete, runnable product: a `customer` actor and a `waiter`
actor (copied from `papeete-product`'s own worked example, so this repo's tests are
self-contained), plus a `product.yaml` declaring `label: alpha, version: latest` for both.

```bash
# stand-in for `papeete-actor build`, tagging in the real {semver}-{label}-{shortSha} shape:
docker build -t customer:0.1.0-alpha-e2e0001 examples/customer
docker build -t waiter:0.1.0-alpha-e2e0001 examples/waiter

papeete-deploy resolve examples/product.yaml
papeete-deploy run examples/product.yaml
curl http://localhost:PORT/order        # PORT printed by run
papeete-deploy stop examples/product.yaml
```

`tests/test_e2e_deploy.py` spawns this same example for real via Docker and asserts both actors
are reachable from outside Docker and that the customer discovers the waiter by name from inside
it:

```bash
uv run --extra dev pytest -m e2e          # needs a Docker daemon
uv run --extra dev pytest -m "not e2e"    # the fast, offline structural suite
```

## Licence

MIT.

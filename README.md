# papeete-deploy

Resolves a [`papeete-product`](https://github.com/papeete-hub/papeete-product)'s declared version
queries against a real registry, and deploys the result. Split out of `papeete-product` because
resolving a query and orchestrating containers both need to touch reality (a registry, a Docker
daemon) in ways a product's own identity contract deliberately refuses — see
[ADR-PD-0001](./adr/ADR-PD-0001-papeete-deploy-is-a-standalone-package.md) and `papeete-product`'s
own `ADR-PP-0002`.

```
papeete-deploy resolve    PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
papeete-deploy deploy     PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
                          [--actor-source {local,git}] [--actor-root PATH]
                          [--actor-git-url URL] [--actor-git-ref REF]
papeete-deploy undeploy   PRODUCT.YAML [--delete-namespace]
```

`resolve` prints each actor's resolved tag, no Docker/kubectl involved — the smaller claim, useful
in CI or for a human to check where a product would land before spending the time to deploy it.
`deploy` resolves the same way, then makes the product real wherever its declared
`environment.type` says: `local` starts every actor via Docker Compose; `k8s` applies each actor's
own kustomize overlay to a real cluster (see "Deploying to k8s" below). `undeploy` tears down
whatever `deploy` started.

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
  implemented and tested; the default (`--registry local`). It qualifies no image name: a local
  image is already called what the manifest calls it.
- **`AcrRegistry`** — every tag an Azure Container Registry has, via `az acr repository
  show-tags`, and the repository those tags belong to. `--registry acr --acr-name NAME` selects
  it; each actor is scoped into the product's own repository path, taken from `product.yaml`'s
  `product:` field (`ADR-PD-0006`). `image_name()` is covered by tests; `list_tags()` still
  **is not wired into CI or exercised by any test** — it needs a live registry to mean anything.

Which registry an actor's declared `environment` (required on every `product.yaml`) should map to
is **not yet automatic** — `--registry`/`--acr-name` are explicit CLI flags for now. See
`ADR-PD-0001`.

## Discovery is Docker's — or Kubernetes' — not invented here

`papeete-deploy deploy` against `environment.type: local` starts every actor on one shared Docker
Compose project, each container named after its `name` (normalized). Compose's own embedded
network DNS resolves that name for every other actor on the same project — no registry lookup, no
sidecar, nothing new built for it. Every actor's server is expected to listen on a fixed port,
`8080`, published to a host-assigned ephemeral port so a caller outside Docker can reach it too.

Against `environment.type: k8s`, each actor's own Kubernetes Service (part of its
`deploy/k8s/base/`, below) gives the same by-name discovery via cluster DNS instead — again,
nothing this package invents. Every k8s object this package applies is renamed with a
product-scoped prefix (below), so an actor calling a sibling by name needs the *prefixed* name —
`examples/customer`'s own `deploy/k8s/base/deployment.yaml` shows the pattern (a plain env var,
authored by the deploy-folder author, same as any other Deployment env var).

## Deploying to k8s

An actor's folder MAY carry `deploy/k8s/base/` + `deploy/k8s/overlays/<recipe>/` — a plain,
actor-authored kustomize layout (`papeete-actor`'s
[`ADR-PA-0025`](https://github.com/papeete-hub/papeete-actor)). **The base Deployment's container
image must be named exactly the actor's own normalized name, with no tag** — the hook this package
uses to inject the resolved image and version at deploy time, without ever editing the actor's own
files (`k8s.py`'s wrapper kustomization, same never-mutate-the-source discipline `deploy.py`'s
`_compose_file()` uses for Compose).

```bash
papeete-deploy deploy PRODUCT.YAML
```

`recipe` (declared per actor in `product.yaml`, `papeete-product`'s own
[`ADR-PP-0003`](https://github.com/papeete-hub/papeete-product)) says which overlay; `environment.
k8sName` (the kubectl context) and `environment.name` (the namespace, created if missing, **never
deleted**) say where. Every actor's deploy folder and overlay is validated to exist *before* any
of them is applied — a missing overlay fails loudly with nothing partially deployed, never a
silent partial rollout.

**Only verified against Docker Desktop's Kubernetes**, whose node shares the host's local image
store — a cluster with its own separate image store would need those images pushed somewhere
reachable first, which this package does not do.

### Locating each actor's deploy folder

Each actor's deploy folder is *located*, never passed on the command line — three tiers, most to
least specific (`ADR-PD-0003`):

1. **A per-actor override** — only in a `papeete-deploy.yaml` next to `product.yaml`:
   ```yaml
   actorDeployOverrides:
     - actor: customer
       type: local
       path: ../wherever/customer          # the actor's own folder, directly
     - actor: waiter
       type: git
       url: https://example.com/waiter-deploy.git
       ref: main
       subpath: waiter                      # optional; defaults to the actor's own name
   ```
2. **A global source** — `papeete-deploy.yaml`'s `actorDeploySource`, layered under env vars
   (`PAPEETE_DEPLOY_ACTOR_SOURCE`/`_ROOT`/`_GIT_URL`/`_GIT_REF`), layered under
   `--actor-source`/`--actor-root`/`--actor-git-url`/`--actor-git-ref` (CLI wins):
   ```yaml
   actorDeploySource:
     type: git                              # or: local, with an optional `root`
     url: https://example.com/deploy-repo.git
     ref: main                              # optional
   ```
   For `type: git`, each actor's deploy folder is expected at `<url>/<actor-name>/deploy` — one
   shared repo, cloned once (`git clone --depth 1`) and reused for every actor that shares it.
3. **The zero-config default** — a sibling folder of `product.yaml`, named exactly the actor's
   own declared `name`, containing `deploy/k8s/overlays/<recipe>` — exactly this repo's own
   `examples/` layout, which is why the worked example below needs no flags at all.

### Product-level resources

An actor's own overlay is the only thing `deploy()` applied until now — there was no way to
express a resource that belongs to the *product* as a whole (a shared dashboard covering every
actor, say), without bolting it onto one actor's lifecycle or duplicating it into every actor's
overlay (`ADR-PD-0004`).

A **product** MAY carry its own `deploy/k8s/base/` + `deploy/k8s/overlays/<recipe>/` too — a
sibling `deploy/` folder next to `product.yaml` itself, same kustomize shape as an actor's,
zero-config only (no override tiers). It's entirely opt-in: set `environment.recipe` to say which
overlay to use; leave it out and nothing product-level is applied. When set, the folder+overlay is
validated to exist in the same pre-flight pass as every actor's own overlay — before anything is
applied, exactly like a missing actor overlay.

```yaml
environment:
  type: k8s
  name: papeete-deploy-example
  k8sName: docker-desktop
  recipe: develop   # selects examples/deploy/k8s/overlays/develop
```

**Every k8s object this package applies — an actor's or the product's — is renamed with a
`<normalized-product-name>-` prefix**, so two products sharing a namespace/cluster never collide
on bare object names (the k8s analogue of Compose's own `<project>-<name>` container naming).
Kustomize's built-in reference-fixup keeps in-overlay references (an Ingress's backend Service
name, say) correct automatically. This prefix only ever touches k8s object *names* it can see — a
literal string value inside a ConfigMap or an env var (an external resource name, like a RabbitMQ
queue) is untouched; making those product-specific, if needed, is the deploy-folder author's own
job.

**Every Ingress path is also prefixed, generated the same way (`ADR-PD-0005`)**: with
`/<normalized-product-name>/<environment.name>/` — the URL-path analogue of the object-name prefix
above, closing the one gap `namePrefix` itself structurally can't reach (a path is a string value,
not an object name/reference). An actor's own `ingress.yaml` authors only its bare, actor-local
path (e.g. `/customer/api(/|$)(.*)`) and must start with a leading `/`; the product+namespace
segments are injected at apply time, never hand-typed. Any other literal string value (a ConfigMap
entry, an env var) remains the deploy-folder author's own responsibility, as above.

## The worked example

[`examples/`](./examples/) is a complete, runnable product: a `customer` actor and a `waiter`
actor (copied from `papeete-product`'s own worked example, so this repo's tests are
self-contained, each also carrying a `deploy/k8s/` folder), plus two `product.yaml` variants —
same actors, different `environment` — declaring `label: alpha, version: latest` for both:
[`productDocker.yaml`](./examples/productDocker.yaml) (`environment.type: local`) and
[`productK8s.yaml`](./examples/productK8s.yaml) (`environment.type: k8s`, targeting the
`docker-desktop` context, `recipe: develop` per actor).

```bash
# stand-in for `papeete-actor build`, tagging in the real {semver}-{label}-{shortSha} shape:
docker build -t customer:0.1.0-alpha-e2e0001 examples/customer
docker build -t waiter:0.1.0-alpha-e2e0001 examples/waiter

# local, via Docker Compose:
papeete-deploy resolve examples/productDocker.yaml
papeete-deploy deploy examples/productDocker.yaml
curl http://localhost:PORT/order        # PORT printed by deploy
papeete-deploy undeploy examples/productDocker.yaml

# local k8s (Docker Desktop's Kubernetes), via kustomize — no flags: examples/'s own layout
# (product.yaml next to customer/ and waiter/) already matches the default convention:
papeete-deploy deploy examples/productK8s.yaml
kubectl -n papeete-deploy-example get deploy,svc -l papeete-deploy/product=pd-table-service
papeete-deploy undeploy examples/productK8s.yaml

# an ephemeral instance, whose whole namespace exists to be thrown away (ADR-PD-0007):
papeete-deploy undeploy examples/productK8s.yaml --delete-namespace
```

`tests/test_e2e_deploy.py` spawns `productDocker.yaml` for real via Docker Compose and asserts
both actors are reachable from outside Docker and that the customer discovers the waiter by name
from inside it. `tests/test_e2e_k8s.py` does the same against a real k8s cluster (`kubectl config
current-context`, its own throwaway product/namespace rather than `productK8s.yaml`, so repeated
test runs never collide) — deploys via `deploy.deploy(..., actor_source=...)` pointed at this
repo's own `examples/`, waits for both pods Ready, execs into the customer pod to prove it reaches
the waiter by Service name, then undeploys and confirms the namespace survives:

```bash
uv run --extra dev pytest -m e2e          # needs a Docker daemon; k8s cases also need a context
uv run --extra dev pytest -m "not e2e"    # the fast, offline structural suite
```

## Releasing

Tag-triggered, via [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC).
**No API token is stored anywhere** — GitHub mints a short-lived OIDC token per run and PyPI trades
it for an upload token. There is nothing to rotate and nothing to leak.

```bash
git tag v0.1.1 && git push origin v0.1.1     # .github/workflows/release.yml does the rest
```

### One-time setup — **not done yet**

`papeete-deploy` is already claimed on PyPI (uploaded manually, outside this repo's CI), so this
is the *existing-project* flow, not the pending-publisher one `papeete-actor`'s README documents
for a project that doesn't exist yet — someone with owner/maintainer rights on the PyPI project
has to do this from its own settings page, which nothing here can do on your behalf:

**1. A publisher on PyPI's existing-project settings.** At
`https://pypi.org/manage/project/papeete-deploy/settings/publishing/`, add a **GitHub** publisher:

| Field | Value |
|---|---|
| Owner | `papeete-hub` |
| Repository name | `papeete-deploy` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

All four must match exactly — PyPI checks the OIDC claims against them and rejects the upload
otherwise. `release.yml` already declares `permissions: id-token: write` and `environment: pypi`,
which is what makes those claims present.

**2. The `pypi` GitHub environment.** No secrets needed in it — it exists so the OIDC claim
carries an environment name for PyPI to match. Protection rules are worth considering, since a
release is irreversible: PyPI never allows re-uploading a version, even after a delete. Required
reviewers, and restricting deployments to tags matching `v*`, are the two that earn their keep.

Until step 1 is done, `release.yml` will run but its `publish to PyPI` step will fail (no trusted
publisher recognizes this workflow's OIDC token yet).

### What a release does — and doesn't — verify

The workflow builds and publishes; it does **not** install the built wheel into a clean
environment first (the pattern `papeete-actor`'s release workflow uses to prove its contract
survived packaging). `papeete-deploy` has no analogous `contracts` command, and — more
importantly — a clean-env install would currently fail anyway: `papeete-product`, a real
dependency, has no PyPI release of its own yet (`ci.yml`'s own comment works around that with a
sibling checkout + `[tool.uv.sources]` path override — a local-dev-only resolution aid that
doesn't apply to `pip install papeete-deploy` from PyPI). Anyone installing `papeete-deploy` from
PyPI today hits that same gap. Closing it means `papeete-product` needs its own PyPI release
first — tracked as an open item, not solved here.

## Licence

MIT.

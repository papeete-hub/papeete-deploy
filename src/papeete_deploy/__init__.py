"""papeete-deploy — resolves a papeete-product's declared version queries against a real
registry, and deploys the result.

    papeete-product/v0 in, a running product out   -> papeete-deploy resolve | run | stop

`product.yaml` names each actor by identity and a query (`label`/`version`/`featureName`), never
a location, never a pre-resolved tag (`papeete-product`'s `ADR-PP-0002`). This package is the one
allowed to know both "where" — which registry an `environment` maps to — and "now" — a registry's
live tag list, a Docker daemon's live state — deliberately, because `papeete-product` and
`papeete-actor` each refuse one or both on purpose (`ADR-PD-0001`).

FRAMED AS DEPLOYMENT, SCOPED NARROWLY FOR NOW. Only a local-Docker registry and local Docker
Compose orchestration are implemented; an Azure Container Registry backend is sketched but
unwired (`registry.py`), and multiple environments/targets (k8s, Terraform, DB migrations) are
explicitly out of scope today — see `ADR-PD-0001`'s consequences.
"""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("papeete-deploy")
except PackageNotFoundError:      # running from a source tree that was never installed
    __version__ = "0.0.0+source"

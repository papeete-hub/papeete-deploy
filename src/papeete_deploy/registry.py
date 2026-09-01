"""Where a resolved version query is actually answered from — a registry's own tag list.

RESOLUTION IS REGISTRY-BASED, NEVER GIT-BASED. An earlier draft of this package would have
resolved `product.yaml`'s declared queries via `papeete_version.match_version()`, which folds a
query against git state in an actor's own folder — but that needs a folder, and this ecosystem's
products deliberately never carry one (`papeete-product`'s `ADR-PP-0001`). Listing tags a
registry already has for a name, filtering by ciType/branch, and taking the newest match sidesteps
that entirely: no folder is needed, only registry access, which deploying anything already needs.
See `ADR-PD-0001` for the full rationale, including the prior art this pattern came from.
"""
import subprocess
from typing import Protocol

from papeete_version import version as pv


class Registry(Protocol):
    def list_tags(self, name: str) -> list[str]:
        """Every tag this registry currently has for `name`, newest first."""
        ...

    def image_name(self, name: str) -> str | None:
        """The repository an actor's image is PULLED from, fully qualified — or None when images
        need no qualifying, as with a daemon's own local store. A base manifest names its
        container by the actor's bare normalized name and nothing else (`ADR-PA-0025`); this is
        what a wrapper rewrites that to, so the same manifest can be deployed against any
        registry without being edited (`ADR-PD-0006`)."""
        ...


class LocalDockerRegistry:
    """Every tag the local Docker daemon already has for `name`, newest-tagged first.

    FULLY IMPLEMENTED AND TESTED — the only backend this package actually exercises today.
    """

    def image_name(self, name: str) -> None:
        """None: a locally-built image is already named exactly what the base manifest names, and
        there is no registry host to put in front of it."""
        return None

    def list_tags(self, name: str) -> list[str]:
        repo = pv.normalize_name(name)
        result = subprocess.run(
            ["docker", "image", "ls", repo, "--format", "{{.Tag}}"],
            capture_output=True, text=True, check=True,
        )
        tags = [t for t in result.stdout.splitlines() if t and t != "<none>"]

        # `docker image ls`'s own CreatedAt column is the underlying IMAGE's content-creation
        # time, not when THIS tag was applied to it — BuildKit can leave that at epoch-zero for
        # reproducible builds, and two different tags can share one image entirely. `docker
        # image inspect`'s `.Metadata.LastTagTime` is the field that actually answers "when was
        # this tag applied", which is what "newest" has to mean here.
        def last_tag_time(tag: str) -> str:
            out = subprocess.run(
                ["docker", "image", "inspect", f"{repo}:{tag}",
                 "--format", "{{.Metadata.LastTagTime}}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            return out

        return sorted(tags, key=last_tag_time, reverse=True)


class AcrRegistry:
    """Every tag an Azure Container Registry has for `name`, newest first, and the repository
    those tags belong to.

    `repository_prefix` scopes a product's own actors into their own path — the caller passes the
    product's normalized name, so `foundry` + `bnk.rlvr.cap.sup.002.ben-implementation` becomes
    `foundry/bnk.rlvr.cap.sup.002.ben-implementation`. Nothing here invents it (`ADR-PD-0006`).

    `list_tags()` remains the shape `az acr repository show-tags --orderby time_desc` gives and is
    not covered by an offline test; `image_name()` is pure string composition and is.
    """

    def __init__(self, acr_name: str, repository_prefix: str = ""):
        self.acr_name = acr_name
        self.repository_prefix = repository_prefix

    @property
    def login_server(self) -> str:
        return f"{self.acr_name}.azurecr.io"

    def _repository(self, name: str) -> str:
        repo = pv.normalize_name(name)
        return f"{self.repository_prefix}/{repo}" if self.repository_prefix else repo

    def image_name(self, name: str) -> str:
        """`<acr>.azurecr.io/<prefix>/<normalized name>` — no tag: the tag is resolved separately
        and applied by the same kustomize `images` entry."""
        return f"{self.login_server}/{self._repository(name)}"

    def list_tags(self, name: str) -> list[str]:
        repo = self._repository(name)
        result = subprocess.run(
            ["az", "acr", "repository", "show-tags", "--name", self.acr_name,
             "--repository", repo, "--orderby", "time_desc", "--output", "tsv"],
            capture_output=True, text=True, check=True,
        )
        return [line for line in result.stdout.splitlines() if line]

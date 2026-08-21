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


class LocalDockerRegistry:
    """Every tag the local Docker daemon already has for `name`, newest-tagged first.

    FULLY IMPLEMENTED AND TESTED — the only backend this package actually exercises today.
    """

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
    """Every tag an Azure Container Registry has for `name`, newest first.

    SKETCHED TO THE SAME PROTOCOL, NOT EXERCISED. No ACR access from this environment to verify
    against — this is the right shape (`az acr repository show-tags --orderby time_desc`), not a
    tested implementation. A deliberate, flagged follow-up, not a silent gap — see `ADR-PD-0001`.
    """

    def __init__(self, acr_name: str, repository_prefix: str = ""):
        self.acr_name = acr_name
        self.repository_prefix = repository_prefix

    def list_tags(self, name: str) -> list[str]:
        repo = pv.normalize_name(name)
        if self.repository_prefix:
            repo = f"{self.repository_prefix}/{repo}"
        result = subprocess.run(
            ["az", "acr", "repository", "show-tags", "--name", self.acr_name,
             "--repository", repo, "--orderby", "time_desc", "--output", "tsv"],
            capture_output=True, text=True, check=True,
        )
        return [line for line in result.stdout.splitlines() if line]

"""papeete-deploy — the CLI. Resolve a product's declared queries, then deploy it.

    papeete-deploy resolve    PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
    papeete-deploy deploy     PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
                              [--actor-source {local,git}] [--actor-root PATH]
                              [--actor-git-url URL] [--actor-git-ref REF]
    papeete-deploy undeploy   PRODUCT.YAML

`resolve` prints each actor's resolved tag, no Docker/kubectl involved — the smaller claim, for a
CI step or a human to check where a product would land before spending the time to deploy it.
`deploy` resolves the same way, then makes the product real wherever its `environment.type` says:
`local` starts every actor via Compose; `k8s` applies each actor's own `deploy/k8s/overlays/
<recipe>`. `undeploy` tears down what `deploy` started. `--registry` defaults to `local` (the
Docker daemon's own image store); `acr` needs `--acr-name`, and scopes each actor into the
product's own repository path (`ADR-PD-0006`). `environment` is not yet used to auto-select
`--registry` — see this package's `ADR-PD-0001`.

For k8s, each actor's own deploy folder is LOCATED, not passed on the command line: a per-actor
override or a shared source, both optionally set in a `papeete-deploy.yaml` next to `product.yaml`
and/or via `--actor-*`/`PAPEETE_DEPLOY_ACTOR_*` (CLI > env > config file), falling back to a
zero-config convention — a sibling folder of `product.yaml`, named exactly the actor's own name.
See the README's "Deploying to k8s" section, and `ADR-PD-0003`.
"""
import argparse
import sys
from pathlib import Path

import yaml
from papeete_product import product as pp
from papeete_version.version import normalize_name

from . import __version__
from . import actor_source
from . import deploy
from .registry import AcrRegistry, LocalDockerRegistry


def _registry_from_args(args):
    """The registry every query resolves against. For ACR, the product's own name becomes the
    repository prefix its actors live under (`ADR-PD-0006`) — read from the product being
    deployed rather than taken as a flag, because it is not a separate choice: a product's actors
    belong in that product's path by definition."""
    if args.registry == "acr":
        if not args.acr_name:
            print("  FAIL --acr-name is required when --registry acr", file=sys.stderr)
            raise SystemExit(2)
        product = yaml.safe_load(Path(args.product).read_text())
        return AcrRegistry(args.acr_name, repository_prefix=normalize_name(product["product"]))
    return LocalDockerRegistry()


def cmd_resolve(args) -> int:
    rep = pp.lint(args.product)
    if rep.errors:
        return rep.emit("papeete-product gate")
    registry = _registry_from_args(args)
    for actor in deploy.resolve_versions(args.product, registry):
        print(f"  ok   {actor['name']:20} {deploy.image_tag(actor['name'], actor['version'])}")
    return 0


def cmd_deploy(args) -> int:
    rep = pp.lint(args.product)
    if rep.errors:
        return rep.emit("papeete-product gate")
    registry = _registry_from_args(args)
    settings = actor_source.load_settings(
        args.product, cli_type=args.actor_source, cli_root=args.actor_root,
        cli_git_url=args.actor_git_url, cli_git_ref=args.actor_git_ref,
    )
    env_type, target = deploy.deploy(args.product, registry, actor_source=settings)
    if env_type == "local":
        print(f"  ok   {args.product}: started as Compose project '{target}'")
        for actor in deploy.resolve_versions(args.product, registry):
            name = actor["name"]
            host_port = deploy.port(target, name)
            print(f"  ok   {name:20} http://localhost:{host_port}  (reachable inside the "
                  f"product's network as http://{deploy.normalize(name)}:{deploy.PORT})")
    else:
        print(f"  ok   {args.product}: deployed to namespace '{target}'")
    return 0


def cmd_undeploy(args) -> int:
    deploy.undeploy(args.product)
    print(f"  ok   {args.product}: undeployed")
    return 0


def _add_registry_args(p) -> None:
    p.add_argument("--registry", choices=["local", "acr"], default="local",
                   help="which registry to resolve each actor's query against (default: local)")
    p.add_argument("--acr-name", dest="acr_name", default=None,
                   help="the ACR to query — required, and only used, when --registry acr")


def _add_actor_source_args(p) -> None:
    p.add_argument("--actor-source", dest="actor_source", choices=["local", "git"], default=None,
                   help="how to locate each k8s-targeted actor's own deploy folder (default: "
                        "papeete-deploy.yaml's actorDeploySource, or a sibling folder of "
                        "product.yaml named after the actor)")
    p.add_argument("--actor-root", dest="actor_root", type=Path, default=None,
                   help="for --actor-source local: the folder containing <actor-name> "
                        "subfolders (default: product.yaml's own directory)")
    p.add_argument("--actor-git-url", dest="actor_git_url", default=None,
                   help="for --actor-source git: the repo to clone; each actor's deploy folder "
                        "is expected at <repo>/<actor-name>/deploy")
    p.add_argument("--actor-git-ref", dest="actor_git_ref", default=None,
                   help="for --actor-source git: the branch/tag to clone (default: the repo's "
                        "own default branch)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="papeete-deploy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"papeete-deploy {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="print each actor's resolved tag, no Docker involved")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    _add_registry_args(p)
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("deploy", help="resolve, then make every actor a product names real")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    _add_registry_args(p)
    _add_actor_source_args(p)
    p.set_defaults(fn=cmd_deploy)

    p = sub.add_parser("undeploy", help="tear down what deploy started")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    p.set_defaults(fn=cmd_undeploy)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        # A missing schema, an unresolvable query, or a malformed product is USER-FIXABLE, and
        # raises with instructions. A traceback buries them under a stack the reader cannot act
        # on, and reads as a crash in the gate rather than a mistake in the invocation.
        print(f"  FAIL {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

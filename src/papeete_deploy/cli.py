"""papeete-deploy — the CLI. Resolve a product's declared queries, then deploy it.

    papeete-deploy resolve   PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
    papeete-deploy run       PRODUCT.YAML [--registry {local,acr}] [--acr-name NAME]
    papeete-deploy stop      PRODUCT.YAML

`resolve` prints each actor's resolved tag, no Docker involved — the smaller claim, for a CI step
or a human to check where a product would land before spending the time to run it. `run` resolves
the same way, then starts every actor via Compose. `--registry` defaults to `local` (the Docker
daemon's own image store); `acr` needs `--acr-name` and is sketched, not exercised
(`registry.py`). The product's declared `environment` is read and required by
`papeete-product`'s own schema, but not yet used to auto-select `--registry` — see this
package's `ADR-PD-0001`.
"""
import argparse
import sys
from pathlib import Path

from papeete_product import product as pp

from . import __version__
from . import deploy
from .registry import AcrRegistry, LocalDockerRegistry


def _registry_from_args(args):
    if args.registry == "acr":
        if not args.acr_name:
            print("  FAIL --acr-name is required when --registry acr", file=sys.stderr)
            raise SystemExit(2)
        return AcrRegistry(args.acr_name)
    return LocalDockerRegistry()


def cmd_resolve(args) -> int:
    rep = pp.lint(args.product)
    if rep.errors:
        return rep.emit("papeete-product gate")
    registry = _registry_from_args(args)
    for actor in deploy.resolve_versions(args.product, registry):
        print(f"  ok   {actor['name']:20} {deploy.image_tag(actor['name'], actor['version'])}")
    return 0


def cmd_run(args) -> int:
    rep = pp.lint(args.product)
    if rep.errors:
        return rep.emit("papeete-product gate")
    registry = _registry_from_args(args)
    proj = deploy.up(args.product, registry)
    print(f"  ok   {args.product}: started as Compose project '{proj}'")
    for actor in deploy.resolve_versions(args.product, registry):
        name = actor["name"]
        host_port = deploy.port(proj, name)
        print(f"  ok   {name:20} http://localhost:{host_port}  (reachable inside the product's "
              f"network as http://{deploy.normalize(name)}:{deploy.PORT})")
    return 0


def cmd_stop(args) -> int:
    deploy.down(args.product)
    print(f"  ok   {args.product}: stopped")
    return 0


def _add_registry_args(p) -> None:
    p.add_argument("--registry", choices=["local", "acr"], default="local",
                   help="which registry to resolve each actor's query against (default: local)")
    p.add_argument("--acr-name", dest="acr_name", default=None,
                   help="the ACR to query — required, and only used, when --registry acr")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="papeete-deploy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"papeete-deploy {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resolve", help="print each actor's resolved tag, no Docker involved")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    _add_registry_args(p)
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("run", help="resolve, then start every actor a product names")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    _add_registry_args(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("stop", help="tear down what run started")
    p.add_argument("product", type=Path, help="path to a product.yaml")
    p.set_defaults(fn=cmd_stop)

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

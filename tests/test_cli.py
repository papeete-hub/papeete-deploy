"""The CLI — argument handling, offline. Docker/registry paths are covered by test_e2e_deploy.py."""
import yaml

from papeete_deploy import cli


def write_product(tmp_path, actors, environment={"type": "local", "name": "local"}):
    p = tmp_path / "product.yaml"
    p.write_text(yaml.safe_dump(
        {"product": "demo", "version": "0.1.0", "environment": environment, "actors": actors}))
    return p


def test_resolve_gates_on_the_product_contract_first(tmp_path, capsys):
    p = write_product(tmp_path, [{"name": "Archivist"}])
    assert cli.main(["resolve", str(p)]) == 1
    assert "missing required key 'label'" in capsys.readouterr().err


def test_deploy_gates_on_the_product_contract_first(tmp_path, capsys):
    p = write_product(tmp_path, [{"name": "Archivist"}])
    assert cli.main(["deploy", str(p)]) == 1
    assert "missing required key 'label'" in capsys.readouterr().err


def test_registry_acr_without_acr_name_is_a_cli_error(tmp_path, capsys):
    p = write_product(tmp_path, [{"name": "archivist", "label": "alpha", "version": "latest"}],
                       environment={"type": "local", "name": "local"})
    try:
        cli.main(["resolve", str(p), "--registry", "acr"])
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 2
    assert "--acr-name is required" in capsys.readouterr().err


def test_a_missing_product_is_reported_not_raised(tmp_path, capsys):
    assert cli.main(["resolve", str(tmp_path / "nope.yaml")]) == 1
    assert "does not parse or cannot be read" in capsys.readouterr().err

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "docs" / "lexicons" / "publish.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("publish_lexicons", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_lexicons_lists_valid_documents():
    mod = _load_script()
    nsids = [nsid for nsid, doc in mod.load_lexicons()]
    assert "net.neodb.defs" in nsids
    assert "net.neodb.mark" in nsids
    assert "net.neodb.profile" in nsids
    assert "net.neodb.review" in nsids


def test_dry_run_does_not_publish():
    mod = _load_script()
    assert mod.main(["--dry-run"]) == 0  # exits before any network access


def test_authority_domain_derivation():
    mod = _load_script()
    assert mod.authority_domain("net.neodb.mark") == "neodb.net"

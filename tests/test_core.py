import json
import os

import pytest

import bambustudio_import as bi
from helpers import FakeBundle, FakeBundles, FakeHost, FakePreset


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


@pytest.fixture
def env(tmp_path):
    bambu = tmp_path / "BambuStudio"
    acc = bambu / "user" / "123"
    orca_dir = tmp_path / "OrcaSlicer"
    os.makedirs(orca_dir / "user" / "default")
    paths = {
        "ready": write(str(acc / "filament" / "Ready.json"), {"name": "Ready", "inherits": "Generic PLA", "odd": 1}),
        "base": write(str(acc / "filament" / "base" / "My Base.json"), {"name": "My Base", "inherits": "Generic PLA"}),
        "child": write(str(acc / "filament" / "Child.json"), {"name": "Child", "inherits": "My Base"}),
        "orphan": write(str(acc / "filament" / "Orphan.json"), {"name": "Orphan", "inherits": "Nope"}),
        "proc": write(str(acc / "process" / "P.json"), {"name": "P", "inherits": "0.20mm Standard @BBL A1"}),
    }
    bundle = FakeBundle(filaments=[FakePreset("Generic PLA", ["name", "inherits"])],
                        prints=[FakePreset("0.20mm Standard @BBL A1", ["layer_height"])])
    bundles = FakeBundles(ids=["bambustudio_import"])
    host = FakeHost(bundle=bundle, bundles=bundles)
    core = bi.ImportCore(host, str(orca_dir), [str(bambu)])
    bdir = bi.bundle_dir(str(orca_dir), "default")
    return core, paths, bundles, bdir, host


def test_scan_returns_items_and_accounts(env):
    core, paths, _, bdir, _ = env
    r = core.handle({"action": "scan"})
    assert r["type"] == "scan"
    assert r["accounts"] == [{"id": "BambuStudio/123", "label": "BambuStudio · 123", "count": 5}]
    assert r["account"] == "BambuStudio/123"
    assert r["bundle_dir"] == bdir
    status = {i["name"]: i["status"] for i in r["items"]}
    assert status == {"Child": "ready", "My Base": "ready", "Orphan": "missing_parent",
                      "Ready": "ready", "P": "ready"}
    item = next(i for i in r["items"] if i["name"] == "Orphan")
    assert item == {"path": paths["orphan"], "kind": "filament", "name": "Orphan", "inherits": "Nope",
                    "status": "missing_parent", "reason": "Parent 'Nope' bestaat niet in OrcaSlicer",
                    "selectable": False, "checked": False, "material": "", "colour": "", "printer": "", "vendor": "Orphan",
                    "visible_on": [], "hidden_on_current": False, "current_printer": "", "bambu_parent": "",
                    "stored": ""}


def test_scan_host_not_ready(env):
    core, *_ = env
    core.host = FakeHost(not_ready=True)
    assert core.handle({"action": "scan"}) == {"type": "error",
                                               "message": "OrcaSlicer is nog aan het opstarten, probeer opnieuw"}


def test_scan_no_bambu(tmp_path):
    core = bi.ImportCore(FakeHost(), str(tmp_path), [])
    r = core.handle({"action": "scan"})
    assert r["accounts"] == [] and r["items"] == []
    assert r["message"].startswith("BambuStudio niet gevonden in ")


def test_import_writes_and_reloads(env):
    core, paths, bundles, bdir, _ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"], paths["proc"]]})
    assert r["type"] == "import"
    assert {(x["name"], x["ok"]) for x in r["results"]} == {("Ready", True), ("P", True)}
    ready = next(x for x in r["results"] if x["name"] == "Ready")
    assert ready["dropped"] == ["odd"]
    assert os.path.exists(os.path.join(bdir, "filament", "Ready.json"))
    assert os.path.exists(os.path.join(bdir, "process", "P.json"))
    assert os.path.exists(os.path.join(bdir, "bundle_metadata.json"))
    assert bundles.reloaded == ["bambustudio_import"]
    assert r["reload"] == {"ok": True, "message": "Profielen geladen", "restart": False}


def test_import_child_without_parent_is_flattened(env):
    core, paths, _, bdir, _ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["child"], paths["ready"]]})
    res = {x["name"]: x for x in r["results"]}
    assert res["Child"]["ok"] is True and res["Ready"]["ok"] is True
    with open(os.path.join(bdir, "filament", "Child.json"), encoding="utf-8") as fh:
        assert json.load(fh)["inherits"] == "Generic PLA"


def test_import_child_with_parent(env):
    core, paths, _, bdir, _ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["child"], paths["base"]]})
    assert all(x["ok"] for x in r["results"])
    assert os.path.exists(os.path.join(bdir, "filament", "Child.json"))


def test_import_child_when_parent_already_imported(env):
    core, paths, _, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["base"]]})
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["child"]]})
    assert r["results"][0]["ok"] is True


def test_import_ignores_missing_parent_even_if_sent(env):
    core, paths, _, bdir, _ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["orphan"]]})
    assert r["results"][0]["ok"] is False
    assert r["results"][0]["reason"] == "Parent 'Nope' bestaat niet in OrcaSlicer"
    assert not os.path.exists(os.path.join(bdir, "filament", "Orphan.json"))


def test_import_unknown_path(env):
    core, *_ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": ["/elders/x.json"]})
    assert r["results"] == [{"name": "x", "kind": "", "ok": False, "reason": "Niet gevonden in BambuStudio",
                             "dropped": []}]


def test_clear(env):
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    r = core.handle({"action": "clear"})
    assert r == {"type": "clear", "ok": True, "message": "Profielen geladen", "restart": False}
    assert not os.path.exists(os.path.join(bdir, "filament"))
    assert bundles.reloaded == ["bambustudio_import", "bambustudio_import"]


def test_unknown_action(env):
    core, *_ = env
    assert core.handle({"action": "boom"}) == {"type": "error", "message": "Onbekende actie: boom"}


def test_exception_becomes_error(env):
    core, *_ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": 5})
    assert r["type"] == "error"


def test_scan_items_carry_display_meta(env):
    core, paths, *_ = env
    write(paths["ready"], {"name": "Ready", "inherits": "Generic PLA", "default_filament_colour": ["#00FF00"],
                           "compatible_printers": ["Bambu Lab P2S 0.4 nozzle"]})
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["material"] == "PLA"
    assert item["colour"] == "#00FF00"
    assert item["printer"] == "P2S"


def test_details(env):
    core, paths, *_ = env
    r = core.handle({"action": "details", "account": "BambuStudio/123", "path": paths["ready"]})
    assert r["type"] == "details"
    assert r["path"] == paths["ready"]
    assert r["name"] == "Ready"
    assert r["dropped"] == ["odd"]
    keys = [k for k, _ in r["settings"]]
    assert "odd" not in keys and "inherits" in keys


def test_details_missing_parent_has_no_conversion(env):
    core, paths, *_ = env
    r = core.handle({"action": "details", "account": "BambuStudio/123", "path": paths["orphan"]})
    assert r["status"] == "missing_parent"
    assert r["dropped"] == [] and r["settings"] == []


def test_details_unknown_path(env):
    core, *_ = env
    r = core.handle({"action": "details", "account": "BambuStudio/123", "path": "/nope.json"})
    assert r == {"type": "error", "message": "Profiel niet gevonden"}


def test_scan_vendor_canonical_across_items(env):
    core, paths, *_ = env
    write(paths["ready"], {"name": "eSun Ready", "inherits": "Generic PLA", "filament_vendor": ["eSun"]})
    write(paths["base"], {"name": "My Base", "inherits": "Generic PLA", "filament_vendor": ["eSUN"]})
    write(paths["child"], {"name": "eSUN Child", "inherits": "My Base"})
    items = {i["name"]: i for i in core.handle({"action": "scan"})["items"]}
    assert items["eSun Ready"]["vendor"] == items["My Base"]["vendor"] == items["eSUN Child"]["vendor"] == "eSUN"
    assert items["P"]["vendor"] == ""

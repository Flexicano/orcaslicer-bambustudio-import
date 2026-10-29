import json
import os

import pytest

import bambustudio_import as bi
from helpers import FakeBundle, FakeBundles, FakeHost, FakePreset, index, no_imports, sp

P2S = "Bambu Lab P2S 0.4 nozzle"
A1 = "Bambu Lab A1 0.4 nozzle"


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
        "plain": write(str(acc / "filament" / "Plain.json"), {"name": "Plain", "inherits": "Generic PLA"}),
        "p2s": write(str(acc / "filament" / "OnP2S.json"),
                     {"name": "OnP2S", "inherits": "Generic PLA", "compatible_printers": [P2S]}),
        "base": write(str(acc / "filament" / "base" / "My Base.json"), {"name": "My Base", "inherits": "Generic PLA"}),
        "child": write(str(acc / "filament" / "Child.json"), {"name": "Child", "inherits": "My Base"}),
        "dup": write(str(acc / "filament" / "Generic PETG.json"), {"name": "Generic PETG", "inherits": "Generic PETG"}),
    }
    bundle = FakeBundle(
        filaments=[FakePreset("Generic PLA", ["name", "inherits", "nozzle_temperature"],
                              values={"compatible_printers": [A1]}),
                   FakePreset("Generic PETG", ["name"])],
        printers=[FakePreset(P2S), FakePreset(A1)], selected_printer=P2S)
    bundles = FakeBundles(ids=["bambustudio_import"])
    host = FakeHost(bundle=bundle, bundles=bundles)
    core = bi.ImportCore(host, str(orca_dir), [str(bambu)], lang="en")
    return core, paths, bundles, bi.bundle_dir(str(orca_dir), "default")


def items(core):
    return {i["name"]: i for i in core.handle({"action": "scan"})["items"]}


# --- 1. languages -----------------------------------------------------------

def test_lang_from_host_code():
    assert bi.lang_from("nl_NL") == "nl"
    assert bi.lang_from("nl") == "nl"
    assert bi.lang_from("en_US") == "en"
    assert bi.lang_from("de_DE") == "en"
    assert bi.lang_from(None) == "en"


def test_every_string_has_both_languages():
    assert set(bi.STRINGS["en"]) == set(bi.STRINGS["nl"])
    assert set(bi.JS_STRINGS["en"]) == set(bi.JS_STRINGS["nl"])


def test_english_reasons(env):
    core, *_ = env
    assert items(core)["Plain"]["reason"] == "Ready to import"


def test_classify_reason_in_english():
    [c] = bi.classify([sp("Mine", "Nope")], index(), no_imports(), lang="en")
    assert c.reason == "Parent 'Nope' does not exist in OrcaSlicer"


def test_invalid_json_reason_per_language():
    [nl] = bi.classify([sp("x", data=False, error="boom")], index(), no_imports(), lang="nl")
    [en] = bi.classify([sp("x", data=False, error="boom")], index(), no_imports(), lang="en")
    assert nl.reason == "Ongeldige JSON: boom"
    assert en.reason == "Invalid JSON: boom"


def test_page_html_is_translated():
    en, nl = bi.page_html("en"), bi.page_html("nl")
    assert '<html lang="en">' in en and '<html lang="nl">' in nl
    assert "Clear filters" in en and "Filters wissen" in nl
    assert "@@" not in en and "@@" not in nl
    assert "Filters wissen" not in en


# --- 3. visibility per printer ----------------------------------------------

def test_visibility_inherits_parent_printers(env):
    core, *_ = env
    it = items(core)["Plain"]
    assert it["visible_on"] == [A1]
    assert it["hidden_on_current"] is True
    assert it["current_printer"] == P2S


def test_visibility_own_printers(env):
    core, *_ = env
    it = items(core)["OnP2S"]
    assert it["visible_on"] == [P2S]
    assert it["hidden_on_current"] is False


def test_visibility_through_bambu_parent(env):
    core, *_ = env
    assert items(core)["Child"]["visible_on"] == [A1]


def test_visibility_all_printers_when_unrestricted(env):
    core, *_ = env
    it = items(core)["Generic PETG"]
    assert it["visible_on"] == [] and it["hidden_on_current"] is False


# --- 4. updated in BambuStudio ----------------------------------------------

def test_outdated_after_bambu_change(env):
    core, paths, _, _ = env
    core.handle({"action": "import", "paths": [paths["plain"]]})
    assert items(core)["Plain"]["status"] == "imported"
    write(paths["plain"], {"name": "Plain", "inherits": "Generic PLA", "nozzle_temperature": ["230"]})
    it = items(core)["Plain"]
    assert it["status"] == "outdated"
    assert it["reason"] == "Changed in BambuStudio since import — tick to update"
    assert it["selectable"] and it["checked"]


# --- 5. small fixes -----------------------------------------------------------

def test_write_error_is_reported_per_preset_and_rest_loads(env, monkeypatch):
    core, paths, bundles, bdir = env
    real = bi._write_json_atomic

    def flaky(path, obj):
        if obj["name"] == "Plain":
            raise OSError("disk full")
        return real(path, obj)
    monkeypatch.setattr(bi, "_write_json_atomic", flaky)
    r = core.handle({"action": "import", "paths": [paths["plain"], paths["p2s"]]})
    res = {x["name"]: x for x in r["results"]}
    assert res["Plain"]["ok"] is False and "disk full" in res["Plain"]["reason"]
    assert res["OnP2S"]["ok"] is True
    assert bundles.reloaded == ["bambustudio_import"]


def test_failed_write_leaves_no_tmp(tmp_path, monkeypatch):
    target = str(tmp_path / "x.json")
    with pytest.raises(TypeError):
        bi._write_json_atomic(target, {"bad": object()})
    assert os.listdir(str(tmp_path)) == []


def test_bambu_parent_flag(env):
    core, *_ = env
    its = items(core)
    assert its["Child"]["bambu_parent"] == "My Base"
    assert its["Plain"]["bambu_parent"] == ""


def test_clear_when_nothing_imported(env):
    core, _, bundles, _ = env
    r = core.handle({"action": "clear"})
    assert r == {"type": "clear", "ok": True, "message": "Nothing to clear", "restart": False}
    assert bundles.reloaded == []


def test_import_without_paths_key(env):
    core, *_ = env
    r = core.handle({"action": "import"})
    assert r["type"] == "import" and r["results"] == []


# --- 6. rename on name conflict -----------------------------------------------

def test_rename_conflicts(env):
    core, paths, _, bdir = env
    r = core.handle({"action": "import", "paths": [paths["dup"]], "rename_conflicts": True})
    assert r["results"][0]["ok"] is True
    assert r["results"][0]["name"] == "Generic PETG (Bambu)"
    with open(os.path.join(bdir, "filament", "Generic PETG (Bambu).json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["name"] == "Generic PETG (Bambu)"
    assert data["filament_settings_id"] == ["Generic PETG (Bambu)"]
    assert items(core)["Generic PETG"]["status"] == "imported"


def test_rename_off_keeps_name(env):
    core, paths, _, bdir = env
    core.handle({"action": "import", "paths": [paths["dup"]]})
    assert os.path.exists(os.path.join(bdir, "filament", "Generic PETG.json"))


def test_children_never_inherit_from_a_renamed_user_preset(env, tmp_path):
    core, paths, _, bdir = env
    base_dup = write(os.path.join(os.path.dirname(paths["plain"]), "GP.json"),
                     {"name": "Generic PETG", "inherits": "Generic PLA"})
    kid = write(os.path.join(os.path.dirname(paths["plain"]), "Kid.json"), {"name": "Kid", "inherits": "Generic PETG"})
    os.remove(paths["dup"])
    core.handle({"action": "import", "paths": [base_dup, kid], "rename_conflicts": True})
    with open(os.path.join(bdir, "filament", "Kid.json"), encoding="utf-8") as fh:
        assert json.load(fh)["inherits"] == "Generic PETG"   # Orca's system preset

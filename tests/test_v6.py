"""v0.6: bulk update, diff, backup/restore, export files, rename prefix, printer check."""
import base64
import io
import json
import os
import zipfile

from test_core import env, write  # noqa: F401  (fixture)

import bambustudio_import as bi
from helpers import FakePreset

ACC = "BambuStudio/123"


def item(core, name, account=ACC):
    return next(i for i in core.handle({"action": "scan", "account": account})["items"] if i["name"] == name)


def change_ready(paths, **values):
    with open(paths["ready"], encoding="utf-8") as fh:
        data = json.load(fh)
    data.update(values)
    write(paths["ready"], data)


# -- 1. bulk update ----------------------------------------------------------

def test_page_has_update_all_button():
    assert 'id="updateOutdated"' in bi.PAGE_HTML
    assert 'it.status==="outdated"' in bi.PAGE_HTML
    for lang in ("en", "nl"):
        assert "{n}" in bi.JS_STRINGS[lang]["update_outdated"]


def test_outdated_bulk_import_updates_where_stored(env):  # noqa: F811
    core, paths, _, bdir, host = env
    host._bundle.filaments._presets[0]._keys.append("nozzle_temperature")
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "target": "user"})
    change_ready(paths, nozzle_temperature=["230"])
    assert item(core, "Ready")["status"] == "outdated"
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "target": "bundle"})
    assert r["results"][0]["stored"] == "user"
    assert item(core, "Ready")["status"] == "imported"


# -- 2. differences before updating ------------------------------------------

def test_details_lists_changes_against_imported_version(env):  # noqa: F811
    core, paths, _, _, host = env
    host._bundle.filaments._presets[0]._keys += ["nozzle_temperature", "hot_plate_temp"]
    change_ready(paths, nozzle_temperature=["220"], hot_plate_temp=["55"])
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    change_ready(paths, nozzle_temperature=["215"], hot_plate_temp=["55"])
    d = core.handle({"action": "details", "account": ACC, "path": paths["ready"]})
    assert d["status"] == "outdated"
    assert d["changes"] == [["nozzle_temperature", "220", "215"]]


def test_details_change_for_new_and_removed_key(env):  # noqa: F811
    core, paths, _, _, host = env
    host._bundle.filaments._presets[0]._keys += ["nozzle_temperature", "hot_plate_temp"]
    change_ready(paths, nozzle_temperature=["220"])
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "target": "user"})
    data = json.load(open(paths["ready"], encoding="utf-8"))
    del data["nozzle_temperature"]
    data["hot_plate_temp"] = ["60"]
    write(paths["ready"], data)
    d = core.handle({"action": "details", "account": ACC, "path": paths["ready"]})
    assert d["changes"] == [["nozzle_temperature", "220", ""], ["hot_plate_temp", "", "60"]]


def test_details_no_changes_for_not_imported(env):  # noqa: F811
    core, paths, _, _, _ = env
    d = core.handle({"action": "details", "account": ACC, "path": paths["ready"]})
    assert d["changes"] == [] and d["backup"] is None


# -- 3. backup and restore ---------------------------------------------------

def _import_change_reimport(core, paths, host, target="bundle"):
    host._bundle.filaments._presets[0]._keys.append("nozzle_temperature")
    change_ready(paths, nozzle_temperature=["220"])
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "target": target})
    change_ready(paths, nozzle_temperature=["215"])
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "target": target})


def test_update_keeps_backup_and_restore_puts_it_back(env):  # noqa: F811
    core, paths, bundles, bdir, host = env
    _import_change_reimport(core, paths, host)
    it = item(core, "Ready")
    assert it["has_backup"] is True and it["status"] == "imported"
    assert core.handle({"action": "details", "account": ACC, "path": paths["ready"]})["backup"]
    bundles.reloaded.clear()
    r = core.handle({"action": "restore", "account": ACC, "path": paths["ready"]})
    assert r["type"] == "restore" and r["ok"] is True and r["restart"] is False
    assert bundles.reloaded == ["bambustudio_import"]
    data = json.load(open(os.path.join(bdir, "filament", "Ready.json"), encoding="utf-8"))
    assert data["nozzle_temperature"] == ["220"]
    it = item(core, "Ready")
    assert it["has_backup"] is False and it["status"] == "outdated"


def test_restore_user_profile_needs_restart(env):  # noqa: F811
    core, paths, _, bdir, host = env
    _import_change_reimport(core, paths, host, target="user")
    r = core.handle({"action": "restore", "account": ACC, "path": paths["ready"]})
    assert r["ok"] is True and r["restart"] is True
    udir = bi.user_dir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(bdir)))), "default")
    data = json.load(open(os.path.join(udir, "filament", "Ready.json"), encoding="utf-8"))
    assert data["nozzle_temperature"] == ["220"]


def test_reimport_without_changes_keeps_no_backup(env):  # noqa: F811
    core, paths, _, _, _ = env
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    assert item(core, "Ready")["has_backup"] is False


def test_restore_without_backup(env):  # noqa: F811
    core, paths, _, _, _ = env
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    r = core.handle({"action": "restore", "account": ACC, "path": paths["ready"]})
    assert r["type"] == "restore" and r["ok"] is False


def test_remove_drops_backup(env):  # noqa: F811
    core, paths, _, _, host = env
    _import_change_reimport(core, paths, host)
    core.handle({"action": "remove", "account": ACC, "paths": [paths["ready"]]})
    core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    assert item(core, "Ready")["has_backup"] is False


# -- 4. BambuStudio export files ---------------------------------------------

def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, obj in members.items():
            z.writestr(name, obj if isinstance(obj, (str, bytes)) else json.dumps(obj))
    return buf.getvalue()


def upload(core, name, raw):
    data = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    return core.handle({"action": "load_file", "name": name, "data": data})


def test_load_bbscfg_becomes_a_source(env):  # noqa: F811
    core, _, _, _, _ = env
    raw = zip_bytes({
        "bundle_structure.json": {"bundle_type": "printer config bundle"},
        "printer/My P1S.json": {"name": "My P1S", "inherits": "Bambu Lab P1S 0.4 nozzle"},
        "filament/Fil X.json": {"name": "Fil X", "inherits": "Generic PLA"},
        "process/Proc Y.json": {"name": "Proc Y", "inherits": "0.20mm Standard @BBL A1"},
    })
    r = upload(core, "My P1S.bbscfg", raw)
    assert r["type"] == "file" and r["account"] == "file/My P1S"
    scan = core.handle({"action": "scan", "account": r["account"]})
    assert scan["account"] == "file/My P1S"
    assert {(i["kind"], i["name"]) for i in scan["items"]} == {
        ("machine", "My P1S"), ("filament", "Fil X"), ("process", "Proc Y")}
    acc = next(a for a in scan["accounts"] if a["id"] == "file/My P1S")
    assert acc["count"] == 3 and "My P1S.bbscfg" in acc["label"]
    fil = next(i for i in scan["items"] if i["name"] == "Fil X")
    imp = core.handle({"action": "import", "account": r["account"], "paths": [fil["path"]]})
    assert imp["results"][0]["ok"] is True


def test_load_bbsflmt_vendor_folders_are_filaments(env):  # noqa: F811
    core, _, _, _, _ = env
    raw = zip_bytes({"bundle_structure.json": {"bundle_type": "filament config bundle"},
                     "Bambu Lab/Fil A @BBL P1S.json": {"name": "Fil A @BBL P1S", "inherits": "Generic PLA"},
                     "Bambu Lab/Fil A @BBL X1C.json": {"name": "Fil A @BBL X1C", "inherits": "Generic PLA"}})
    r = upload(core, "Fil A.bbsflmt", raw)
    kinds = {i["kind"] for i in core.handle({"action": "scan", "account": r["account"]})["items"]}
    assert kinds == {"filament"}


def test_load_presets_zip_uses_settings_id(env):  # noqa: F811
    core, _, _, _, _ = env
    raw = zip_bytes({"A.json": {"name": "A", "inherits": "Generic PLA", "filament_settings_id": ["A"]},
                     "B.json": {"name": "B", "print_settings_id": "B"},
                     "notes.txt": "hello"})
    r = upload(core, "Mixed presets.zip", raw)
    got = {(i["kind"], i["name"]) for i in core.handle({"action": "scan", "account": r["account"]})["items"]}
    assert got == {("filament", "A"), ("process", "B")}


def test_load_single_json(env):  # noqa: F811
    core, _, _, _, _ = env
    raw = json.dumps({"name": "Solo", "inherits": "Generic PLA", "filament_settings_id": ["Solo"]}).encode()
    r = upload(core, "Solo.json", raw)
    assert [i["name"] for i in core.handle({"action": "scan", "account": r["account"]})["items"]] == ["Solo"]


def test_load_garbage_is_an_error(env):  # noqa: F811
    core, _, _, _, _ = env
    r = upload(core, "x.bbscfg", b"not a zip")
    assert r["type"] == "error"


def test_zip_paths_cannot_escape(env, tmp_path):  # noqa: F811
    core, _, _, _, _ = env
    raw = zip_bytes({"filament/../../../evil.json": {"name": "../../evil", "filament_settings_id": ["x"]}})
    r = upload(core, "../../evil.bbscfg", raw)
    files = bi.files_dir(core.orca_dir)
    for root, _dirs, names in os.walk(tmp_path):
        for n in names:
            p = os.path.join(root, n)
            if "evil" in n:
                assert p.startswith(files), p
    assert r["account"].startswith("file/") and "/" not in r["account"][5:]


def test_forget_file(env):  # noqa: F811
    core, _, _, _, _ = env
    r = upload(core, "Solo.json", json.dumps({"name": "Solo", "filament_settings_id": ["Solo"]}).encode())
    f = core.handle({"action": "forget_file", "account": r["account"]})
    assert f["type"] == "file_forgotten"
    ids = [a["id"] for a in core.handle({"action": "scan"})["accounts"]]
    assert r["account"] not in ids


def test_forget_file_refuses_other_paths(env):  # noqa: F811
    core, _, _, _, _ = env
    assert core.handle({"action": "forget_file", "account": "file/.."})["type"] == "error"
    assert core.handle({"action": "forget_file", "account": ACC})["type"] == "error"


# -- 5. name conflicts -------------------------------------------------------

def _conflict(env):
    core, paths, _, bdir, host = env
    host._bundle.filaments._presets.append(FakePreset("Ready", ["name"]))
    assert item(core, "Ready")["status"] == "exists"
    return core, paths, bdir


def test_rename_with_prefix(env):  # noqa: F811
    core, paths, bdir = _conflict(env)
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]],
                     "rename_mode": "prefix", "rename_prefix": "BBS - "})
    assert r["results"][0]["name"] == "BBS - Ready"
    data = json.load(open(os.path.join(bdir, "filament", "BBS - Ready.json"), encoding="utf-8"))
    assert data["name"] == "BBS - Ready" and data["filament_settings_id"] == ["BBS - Ready"]
    it = item(core, "Ready")
    assert it["status"] == "imported" and it["stored"] == "bundle"
    # updating keeps the renamed name
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]]})
    assert r["results"][0]["name"] == "BBS - Ready"
    core.handle({"action": "remove", "account": ACC, "paths": [paths["ready"]]})
    assert not os.path.exists(os.path.join(bdir, "filament", "BBS - Ready.json"))
    assert item(core, "Ready")["status"] == "exists"


def test_rename_suffix_and_legacy_flag(env):  # noqa: F811
    core, paths, _ = _conflict(env)
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "rename_conflicts": True})
    assert r["results"][0]["name"] == "Ready (Bambu)"
    assert item(core, "Ready")["status"] == "imported"


def test_rename_mode_suffix(env):  # noqa: F811
    core, paths, _ = _conflict(env)
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]], "rename_mode": "suffix"})
    assert r["results"][0]["name"] == "Ready (Bambu)"


def test_empty_prefix_means_no_rename(env):  # noqa: F811
    core, paths, _ = _conflict(env)
    r = core.handle({"action": "import", "account": ACC, "paths": [paths["ready"]],
                     "rename_mode": "prefix", "rename_prefix": "  "})
    assert r["results"][0]["name"] == "Ready"


def test_page_has_conflict_controls():
    for i in ("renameMode", "renamePrefix", "conflictWarn"):
        assert 'id="%s"' % i in bi.PAGE_HTML, i
    assert "rename_mode:S.renameMode" in bi.PAGE_HTML


# -- 6. printer check --------------------------------------------------------

def test_profile_for_unknown_printer_is_flagged(env, tmp_path):  # noqa: F811
    core, paths, _, _, _ = env
    acc = os.path.dirname(os.path.dirname(paths["ready"]))
    write(os.path.join(acc, "process", "Q.json"), {"name": "Q", "inherits": "0.20mm Standard @BBL A1",
                                                   "compatible_printers": ["Nonexistent Printer 0.4 nozzle"]})
    q = item(core, "Q")
    assert q["no_printer"] is True and q["checked"] is False and q["status"] == "ready"
    assert item(core, "P")["no_printer"] is False


def test_profile_for_known_or_scanned_printer_is_fine(env):  # noqa: F811
    core, paths, _, _, host = env
    acc = os.path.dirname(os.path.dirname(paths["ready"]))
    host._bundle.printers._presets.append(FakePreset("Bambu Lab A1 0.4 nozzle", ["printer_model"]))
    write(os.path.join(acc, "process", "Q.json"), {"name": "Q", "inherits": "0.20mm Standard @BBL A1",
                                                   "compatible_printers": ["Bambu Lab A1 0.4 nozzle"]})
    write(os.path.join(acc, "process", "R.json"), {"name": "R", "inherits": "0.20mm Standard @BBL A1",
                                                   "compatible_printers": ["My Own Printer"]})
    write(os.path.join(acc, "machine", "My Own Printer.json"), {"name": "My Own Printer"})
    assert item(core, "Q")["no_printer"] is False
    assert item(core, "R")["no_printer"] is False

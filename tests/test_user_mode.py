import json
import os

from test_core import env  # noqa: F401  (fixture)

import bambustudio_import as bi


def user_file(bdir, kind, name):
    udir = os.path.dirname(os.path.dirname(bdir))   # <orca>/user/<folder>
    return os.path.join(udir, kind, name + ".json")


def test_import_as_user_profile_writes_user_folder_without_reload(env):  # noqa: F811
    core, paths, bundles, bdir, _ = env
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    assert [(x["name"], x["ok"], x["stored"]) for x in r["results"]] == [("Ready", True, "user")]
    path = user_file(bdir, "filament", "Ready")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["name"] == "Ready" and data["from"] == "User" and data["inherits"] == "Generic PLA"
    assert not os.path.exists(os.path.join(bdir, "filament", "Ready.json"))
    assert bundles.reloaded == []
    assert r["reload"]["restart"] is True
    assert r["reload"]["message"] == "1 als gebruikersprofiel opgeslagen — herstart OrcaSlicer om ze te zien"


def test_user_import_shows_as_imported_on_rescan(env):  # noqa: F811
    core, paths, _, _, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "imported" and item["stored"] == "user" and item["checked"] is False


def test_user_import_after_restart_is_not_a_conflict(env):  # noqa: F811
    # after a restart Orca lists the user preset: still "imported", not "exists"
    core, paths, _, bdir, host = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    from helpers import FakePreset
    host._bundle.filaments._presets.append(FakePreset("Ready", ["name"], file=user_file(bdir, "filament", "Ready"),
                                                      user=True))
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "imported" and item["stored"] == "user"


def test_user_profile_edited_in_orca_is_not_outdated(env):  # noqa: F811
    core, paths, _, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    path = user_file(bdir, "filament", "Ready")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["nozzle_temperature"] = ["215"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "imported"


def test_user_profile_outdated_when_bambu_changes(env):  # noqa: F811
    core, paths, _, _, host = env
    host._bundle.filaments._presets[0]._keys.append("nozzle_temperature")
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    with open(paths["ready"], "w", encoding="utf-8") as fh:
        json.dump({"name": "Ready", "inherits": "Generic PLA", "nozzle_temperature": ["230"]}, fh)
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "outdated"


def test_reimport_stays_where_it_was(env):  # noqa: F811
    # bundle-imported preset re-imported with target=user keeps living in the bundle
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    assert r["results"][0]["stored"] == "bundle"
    assert not os.path.exists(user_file(bdir, "filament", "Ready"))
    assert bundles.reloaded == ["bambustudio_import", "bambustudio_import"]


def test_deleted_user_profile_is_forgotten(env):  # noqa: F811
    core, paths, _, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    os.remove(user_file(bdir, "filament", "Ready"))
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "ready" and item["stored"] == ""


def test_mixed_import_reloads_bundle_and_asks_restart(env):  # noqa: F811
    core, paths, bundles, _, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["proc"]]})
    r = core.handle({"action": "import", "account": "BambuStudio/123",
                     "paths": [paths["ready"], paths["proc"]], "target": "user"})
    assert {x["name"]: x["stored"] for x in r["results"]} == {"Ready": "user", "P": "bundle"}
    assert bundles.reloaded == ["bambustudio_import", "bambustudio_import"]
    assert r["reload"]["ok"] is True and r["reload"]["restart"] is True


def info_file(bdir, kind, name):
    return user_file(bdir, kind, name)[:-len(".json")] + ".info"


def test_remove_user_profile_keeps_info_for_cloud_delete(env):  # noqa: F811
    # Orca deletes the cloud copy of a preset whose .json is gone but whose .info is left
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    with open(info_file(bdir, "filament", "Ready"), "w", encoding="utf-8") as fh:
        fh.write("sync_info = \nsetting_id = abc\n")
    r = core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    assert r["type"] == "remove"
    assert [(x["name"], x["ok"], x["stored"]) for x in r["results"]] == [("Ready", True, "user")]
    assert r["reload"]["restart"] is True
    assert not os.path.exists(user_file(bdir, "filament", "Ready"))
    assert os.path.exists(info_file(bdir, "filament", "Ready"))
    assert bundles.reloaded == []
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["stored"] == "" and item["status"] == "ready"


def test_remove_bundle_profile_reloads(env):  # noqa: F811
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"], paths["proc"]]})
    r = core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    assert [(x["name"], x["ok"], x["stored"]) for x in r["results"]] == [("Ready", True, "bundle")]
    assert not os.path.exists(os.path.join(bdir, "filament", "Ready.json"))
    assert os.path.exists(os.path.join(bdir, "process", "P.json"))
    assert bundles.reloaded == ["bambustudio_import", "bambustudio_import"]
    assert r["reload"] == {"ok": True, "message": "Profielen geladen", "restart": False}


def test_remove_renamed_profile(env):  # noqa: F811
    core, paths, _, bdir, host = env
    from helpers import FakePreset
    host._bundle.filaments._presets.append(FakePreset("Ready", ["name"]))
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]],
                 "rename_conflicts": True, "target": "user"})
    assert os.path.exists(user_file(bdir, "filament", "Ready (Bambu)"))
    r = core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    assert r["results"][0]["ok"] is True
    assert not os.path.exists(user_file(bdir, "filament", "Ready (Bambu)"))


def test_remove_not_imported_is_skipped(env):  # noqa: F811
    core, paths, bundles, _, _ = env
    r = core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    assert r["results"][0]["ok"] is False
    assert r["reload"]["ok"] is False and bundles.reloaded == []


def test_remove_never_touches_own_orca_presets(env):  # noqa: F811
    # a user preset we didn't write (not in the manifest) is never deleted
    core, paths, _, bdir, _ = env
    own = user_file(bdir, "filament", "Ready")
    os.makedirs(os.path.dirname(own), exist_ok=True)
    with open(own, "w", encoding="utf-8") as fh:
        json.dump({"name": "Ready", "from": "User"}, fh)
    core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    assert os.path.exists(own)


def test_clear_removes_user_profiles_too(env):  # noqa: F811
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["proc"]]})
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    r = core.handle({"action": "clear"})
    assert r["ok"] is True and r["restart"] is True
    assert not os.path.exists(user_file(bdir, "filament", "Ready"))
    assert not os.path.exists(os.path.join(bdir, "process", "P.json"))
    assert bundles.reloaded == ["bambustudio_import", "bambustudio_import"]


def test_clear_only_user_profiles(env):  # noqa: F811
    core, paths, bundles, bdir, _ = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    r = core.handle({"action": "clear"})
    assert r["ok"] is True and r["restart"] is True
    assert r["message"] == "1 gebruikersprofiel verwijderd — herstart OrcaSlicer om het af te ronden"
    assert bundles.reloaded == []


def test_removed_user_profile_still_loaded_says_restart(env):  # noqa: F811
    # until Orca restarts it still lists the removed preset: show that, not "name exists"
    core, paths, _, bdir, host = env
    core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    from helpers import FakePreset
    host._bundle.filaments._presets.append(FakePreset("Ready", ["name"], file=user_file(bdir, "filament", "Ready"),
                                                      user=True))
    core.handle({"action": "remove", "account": "BambuStudio/123", "paths": [paths["ready"]]})
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "ready" and item["checked"] is False
    assert item["reason"] == "Verwijderd — herstart OrcaSlicer om het af te ronden"
    # importing it again works and is a normal user import
    r = core.handle({"action": "import", "account": "BambuStudio/123", "paths": [paths["ready"]], "target": "user"})
    assert r["results"][0]["ok"] is True
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["name"] == "Ready")
    assert item["status"] == "imported" and item["stored"] == "user"

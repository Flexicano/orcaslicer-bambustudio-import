import copy

import bambustudio_import as bi
from helpers import index


def preset(name, inherits, data, kind="filament"):
    d = {"name": name, "inherits": inherits, **data}
    return bi.SourcePreset(kind, name, "/x.json", inherits, d, "")


def test_drops_unknown_keeps_known():
    p = preset("Mine", "Generic PLA", {"nozzle_temperature": ["220"], "filament_extruder_variant": ["Direct"]})
    out, dropped = bi.convert(p, index(filament={"Generic PLA"}, keys={"Generic PLA": {"nozzle_temperature"}}), {})
    assert out["nozzle_temperature"] == ["220"]
    assert "filament_extruder_variant" not in out
    assert dropped == ["filament_extruder_variant"]


def test_always_kept_and_from_user():
    p = preset("Mine", "Generic PLA", {"version": "1.7.0.18", "from": "Bambu"})
    out, _ = bi.convert(p, index(filament={"Generic PLA"}), {})
    assert out["name"] == "Mine"
    assert out["inherits"] == "Generic PLA"
    assert out["version"] == "1.7.0.18"
    assert out["from"] == "User"
    assert out["filament_settings_id"] == ["Mine"]


def test_settings_id_shape_per_kind():
    out, _ = bi.convert(preset("P", "0.20mm", {}, kind="process"), index(process={"0.20mm"}), {})
    assert out["print_settings_id"] == "P"
    out, _ = bi.convert(preset("M", "P2S", {}, kind="machine"), index(machine={"P2S"}), {})
    assert out["printer_settings_id"] == "M"


def test_meta_dropped_silently():
    p = preset("Mine", "Generic PLA", {"setting_id": "X", "base_id": "Y", "user_id": "1",
                                      "updated_time": "2", "instantiation": "true"})
    out, dropped = bi.convert(p, index(filament={"Generic PLA"}), {})
    for k in bi.ALWAYS_DROP:
        assert k not in out
    assert dropped == []


def test_chained_parent_uses_root_keys():
    base = preset("My Base", "Generic PLA", {})
    child = preset("Child", "My Base", {"nozzle_temperature": ["1"], "odd": ["2"]})
    out, dropped = bi.convert(child, index(filament={"Generic PLA"}, keys={"Generic PLA": {"nozzle_temperature"}}),
                              {"My Base": base})
    assert "nozzle_temperature" in out
    assert dropped == ["odd"]


def test_no_inherits_uses_union():
    p = preset("Root", "", {"a": "1", "c": "3"})
    out, dropped = bi.convert(p, index(filament={"X", "Y"}, keys={"X": {"a"}, "Y": {"b"}}), {})
    assert out["a"] == "1"
    assert dropped == ["c"]


def test_input_not_mutated():
    p = preset("Mine", "Generic PLA", {"odd": "1", "setting_id": "S"})
    before = copy.deepcopy(p.data)
    bi.convert(p, index(filament={"Generic PLA"}), {})
    assert p.data == before


def test_chain_is_flattened_onto_orca_parent():
    # Orca only lets presets inherit from system presets, so a Bambu
    # user parent is merged in and the preset inherits the Orca root.
    base = preset("My Base", "Generic PLA", {"nozzle_temperature": ["240"], "fan_min_speed": ["30"]})
    child = preset("Child", "My Base", {"nozzle_temperature": ["250"]})
    out, _ = bi.convert(child, index(filament={"Generic PLA"},
                                     keys={"Generic PLA": {"nozzle_temperature", "fan_min_speed"}}), {"My Base": base})
    assert out["inherits"] == "Generic PLA"
    assert out["nozzle_temperature"] == ["250"]
    assert out["fan_min_speed"] == ["30"]
    assert out["name"] == "Child"


def test_child_of_base_profile_becomes_standalone():
    base = preset("My Base", "", {"nozzle_temperature": ["240"], "filament_type": ["PETG"]})
    child = preset("Child", "My Base", {"nozzle_temperature": ["245"]})
    out, _ = bi.convert(child, index(filament={"X"}, keys={"X": {"nozzle_temperature", "filament_type"}}),
                        {"My Base": base})
    assert out["inherits"] == ""
    assert out["filament_type"] == ["PETG"]
    assert out["nozzle_temperature"] == ["245"]

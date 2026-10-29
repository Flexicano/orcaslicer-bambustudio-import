"""BambuStudio stores per-extruder-variant values and writes "nil" for "same
as the parent". Orca only accepts "nil" in its nullable (retraction
override) options; elsewhere it rejects the whole bundle."""
import bambustudio_import as bi
from helpers import FakePreset, index


def preset(name, inherits, data, kind="filament"):
    return bi.SourcePreset(kind, name, "/x/%s.json" % name, inherits, {"name": name, "inherits": inherits, **data}, "")


KEYS = {"Generic PLA": {"nozzle_temperature", "filament_flow_ratio", "filament_retraction_length",
                        "filament_extruder_variant"}}


def test_nil_filled_from_sibling_without_parent_value():
    out, _ = bi.convert(preset("A", "Generic PLA", {"nozzle_temperature": ["nil", "245"]}),
                        index(filament={"Generic PLA"}, keys=KEYS), {})
    assert out["nozzle_temperature"] == ["245", "245"]


def test_nil_kept_in_nullable_orca_options():
    out, _ = bi.convert(preset("A", "Generic PLA", {"filament_retraction_length": ["nil", "0.8"]}),
                        index(filament={"Generic PLA"}, keys=KEYS), {})
    assert out["filament_retraction_length"] == ["nil", "0.8"]


def test_all_nil_dropped_so_parent_value_applies():
    out, dropped = bi.convert(preset("A", "Generic PLA", {"filament_flow_ratio": ["nil", "nil"]}),
                              index(filament={"Generic PLA"}, keys=KEYS), {})
    assert "filament_flow_ratio" not in out
    assert "filament_flow_ratio" not in dropped


def test_nil_filled_from_bambu_parent_same_position():
    base = preset("Base", "Generic PLA", {"nozzle_temperature": ["240", "250"]})
    child = preset("Child", "Base", {"nozzle_temperature": ["nil", "245"]})
    out, _ = bi.convert(child, index(filament={"Generic PLA"}, keys=KEYS), {"Base": base})
    assert out["nozzle_temperature"] == ["240", "245"]


def test_nil_filled_from_orca_parent_value():
    idx = index(filament={"Generic PLA"}, keys=KEYS)
    idx.presets = {"filament": {"Generic PLA": FakePreset("Generic PLA", values={"nozzle_temperature": ["230", "260"]})}}
    out, _ = bi.convert(preset("A", "Generic PLA", {"nozzle_temperature": ["nil", "nil", "nil"]}), idx, {})
    assert out["nozzle_temperature"] == ["230", "260", "260"]


def test_orca_parent_value_as_string():
    idx = index(filament={"Generic PLA"}, keys=KEYS)
    idx.presets = {"filament": {"Generic PLA": FakePreset("Generic PLA", values={"nozzle_temperature": "230,260"})}}
    out, _ = bi.convert(preset("A", "Generic PLA", {"nozzle_temperature": ["nil", "250"]}), idx, {})
    assert out["nozzle_temperature"] == ["230", "250"]


def test_real_failing_profile_has_no_nil_outside_nullable():
    data = {"filament_flow_ratio": ["1", "1", "nil"], "filament_max_volumetric_speed": ["12", "12", "nil"],
            "nozzle_temperature_initial_layer": ["250", "250", "nil"], "filament_retraction_length": ["0.8", "0.8"],
            "filament_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow", "Direct Drive E3D High Flow"]}
    keys = {"Bambu PETG Basic @BBL P2S 0.4 nozzle": set(data)}
    out, _ = bi.convert(preset("Geeetech PETG new", "Bambu PETG Basic @BBL P2S 0.4 nozzle", data),
                        index(filament={"Bambu PETG Basic @BBL P2S 0.4 nozzle"}, keys=keys), {})
    for k, v in out.items():
        if isinstance(v, list) and not bi.orca_nullable(k):
            assert "nil" not in v, k

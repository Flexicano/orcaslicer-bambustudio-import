import bambustudio_import as bi


def test_material_from_filament_type():
    assert bi.material_of("eSUN thing", "", {"filament_type": ["PETG"]}) == "PETG"


def test_material_from_names():
    assert bi.material_of("eSUN PLA+ Blue", "Generic PLA", {}) == "PLA"
    assert bi.material_of("eSun eSilk PLA Violet", "Generic PLA Silk", {}) == "PLA"
    assert bi.material_of("SUNLU PETG @ TPU DUMMY", "SUNLU PETG @BBL A1", {}) == "PETG"
    assert bi.material_of("Geeetech ABS Black", "Generic ABS", {}) == "ABS"
    assert bi.material_of("Foo", "Bar", {}) == ""


def test_material_prefers_petg_over_pet():
    assert bi.material_of("X PETG", "", {}) == "PETG"
    assert bi.material_of("PA-CF", "", {}) == "PA"


def test_colour_of():
    assert bi.colour_of({"default_filament_colour": ["#0000FF"]}) == "#0000FF"
    assert bi.colour_of({"filament_colour": ["#ff8800"]}) == "#ff8800"
    assert bi.colour_of({"default_filament_colour": [""]}) == ""
    assert bi.colour_of({"default_filament_colour": ["red;drop table"]}) == ""
    assert bi.colour_of(None) == ""


def test_printer_of():
    assert bi.printer_of("eSUN PLA+ Blue @Bambu Lab P2S 0.4 nozzle", "Generic PLA", {}) == "P2S"
    assert bi.printer_of("0.20mm Standard @BBL A1 - x", "", {}) == "A1"
    assert bi.printer_of("X", "Y", {"compatible_printers": ["Bambu Lab A1 mini 0.4 nozzle"]}) == "A1 mini"
    assert bi.printer_of("Mine", "Generic PLA", {}) == ""


def test_vendor_from_filament_vendor():
    assert bi.vendor_of("eSUN PLA+ Blue", {"filament_vendor": ["eSUN"]}) == "eSUN"


def test_vendor_from_first_word_when_missing():
    assert bi.vendor_of("SUNLU PETG @ TPU DUMMY", {}) == "SUNLU"
    assert bi.vendor_of("Geeetech ABS Black", {"filament_vendor": [""]}) == "Geeetech"


def test_vendor_generic_or_unknown_is_empty():
    assert bi.vendor_of("PLA mine", {"filament_vendor": ["Generic"]}) == ""
    assert bi.vendor_of("PLA mine", {}) == ""
    assert bi.vendor_of("0.20mm Standard", {}) == ""


def test_vendor_names_merge_case_insensitively():
    assert bi.canonical_vendors(["eSUN", "eSun", "eSUN", "SUNLU"]) == {"esun": "eSUN", "sunlu": "SUNLU"}


def test_vendor_alias_bambu():
    assert bi.vendor_of("Bambu PLA Basic @BBL P2S - mine", {}) == "Bambu Lab"


def test_display_settings_skip_empty_values():
    out = bi._display_settings("filament", {"default_filament_colour": [""], "nozzle_temperature": ["220"], "x": ""})
    assert out == [["nozzle_temperature", "220"]]

import bambustudio_import as bi
from helpers import FakeBundle, FakePreset, index, no_imports, sp


def by_name(result):
    return {c.preset.name: c for c in result}


def test_ready_when_parent_in_orca():
    [c] = bi.classify([sp("Mine", "Generic PLA")], index(filament={"Generic PLA"}), no_imports())
    assert c.status == "ready"
    assert c.reason == "Klaar om te importeren"
    assert c.selectable and c.default_checked


def test_missing_parent():
    [c] = bi.classify([sp("Mine", "Bambu PLA Basic @BBL P2S")], index(), no_imports())
    assert c.status == "missing_parent"
    assert c.reason == "Parent 'Bambu PLA Basic @BBL P2S' bestaat niet in OrcaSlicer"
    assert not c.selectable and not c.default_checked


def test_parent_is_other_bambu_user_preset():
    r = by_name(bi.classify([sp("Child", "My Base"), sp("My Base", "Generic PLA")],
                            index(filament={"Generic PLA"}), no_imports()))
    assert r["Child"].status == "ready"
    assert r["My Base"].status == "ready"


def test_chain_with_missing_root():
    r = by_name(bi.classify([sp("Child", "My Base"), sp("My Base", "Nope")], index(), no_imports()))
    assert r["Child"].status == "missing_parent"
    assert r["My Base"].status == "missing_parent"


def test_parent_is_invalid_bambu_preset():
    r = by_name(bi.classify([sp("Child", "Broken"), sp("Broken", data=False, error="Ongeldige JSON: x")],
                            index(), no_imports()))
    assert r["Child"].status == "missing_parent"
    assert r["Broken"].status == "invalid"


def test_parent_lookup_is_per_kind():
    r = by_name(bi.classify([sp("Proc", "X", kind="process"), sp("Y")],
                            index(filament={"X"}, process={"Y"}), no_imports()))
    assert r["Proc"].status == "missing_parent"
    assert r["Y"].status == "ready"


def test_imported_beats_exists():
    imported = no_imports()
    imported["filament"].add("Mine")
    [c] = bi.classify([sp("Mine")], index(filament={"Mine"}), imported)
    assert c.status == "imported"
    assert c.reason == "Al geïmporteerd — aanvinken overschrijft"
    assert c.selectable and not c.default_checked


def test_exists():
    [c] = bi.classify([sp("Generic PLA")], index(filament={"Generic PLA"}), no_imports())
    assert c.status == "exists"
    assert c.reason == "Naam bestaat al in OrcaSlicer — import maakt een tweede preset met deze naam"
    assert c.selectable and not c.default_checked


def test_invalid():
    [c] = bi.classify([sp("bad", data=False, error="boom")], index(), no_imports())
    assert c.status == "invalid"
    assert c.reason == "Ongeldige JSON: boom"
    assert not c.selectable


def test_from_host_maps_collections():
    bundle = FakeBundle(
        filaments=[FakePreset("Generic PLA", ["a", "b"]),
                   FakePreset("Mine", ["a"], file="/o/user/42/filament/Mine.json", user=True)],
        prints=[FakePreset("0.20mm", ["layer_height"])],
        printers=[FakePreset("P2S", ["nozzle"])],
    )
    idx = bi.OrcaIndex.from_host(bundle)
    assert idx.names["filament"] == {"Generic PLA", "Mine"}
    assert idx.names["process"] == {"0.20mm"}
    assert idx.keys["machine"]["P2S"] == {"nozzle"}
    assert idx.user_files == ["/o/user/42/filament/Mine.json"]
    assert idx.union_keys("filament") == {"a", "b"}


def test_from_host_skips_broken_entries():
    class Bad:
        @property
        def name(self):
            raise RuntimeError("gone")
    idx = bi.OrcaIndex.from_host(FakeBundle(filaments=[Bad(), FakePreset("Ok")]))
    assert idx.names["filament"] == {"Ok"}

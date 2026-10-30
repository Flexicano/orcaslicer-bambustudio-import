import json
import os

import bambustudio_import as bi
from helpers import FakeBundle, FakeBundles, FakeHost, FakePreset


def fp(name, vendor=None, inherits="Generic PLA", kind="filament"):
    data = {"name": name, "inherits": inherits}
    if vendor is not None:
        data["filament_vendor"] = [vendor]
    return bi.SourcePreset(kind, name, "/src/%s/%s.json" % (kind, name), inherits, data, "")


def test_brands_fill_missing_vendor_from_name():
    presets = [fp("eSUN PLA+ Red", "eSUN"), fp("eSUN PLA Matte Black"), fp("eSun Silk", "eSun"),
               fp("eSUN PETG", "eSUN")]
    b = bi.brands_of(presets)
    # one spelling (the most common), also for the profile without its own vendor
    assert b == {p.path: "eSUN" for p in presets}


def test_brands_need_an_explicit_vendor_somewhere():
    # "Ready PLA" would otherwise become a brand "Ready"
    presets = [fp("Ready PLA"), fp("Generic thing", "Generic"), fp("PLA Basic")]
    assert bi.brands_of(presets) == {}


def test_brands_only_for_filament():
    presets = [fp("SUNLU PLA", "SUNLU"), fp("SUNLU 0.20mm", kind="process", inherits="")]
    assert set(bi.brands_of(presets).values()) == {"SUNLU"}
    assert len(bi.brands_of(presets)) == 1


def test_import_writes_brand_as_filament_vendor(tmp_path):
    bambu = tmp_path / "BambuStudio"
    acc = bambu / "user" / "1" / "filament"
    os.makedirs(acc)
    for name, data in (("eSUN PLA+ Red", {"filament_vendor": ["eSUN"]}), ("eSUN PLA Matte Black", {})):
        with open(acc / (name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(dict(name=name, inherits="Generic PLA", **data), fh)
    orca = tmp_path / "OrcaSlicer"
    os.makedirs(orca / "user" / "default")
    host = FakeHost(bundle=FakeBundle(filaments=[FakePreset("Generic PLA", ["name", "filament_vendor"])]),
                    bundles=FakeBundles(ids=["bambustudio_import"]))
    core = bi.ImportCore(host, str(orca), [str(bambu)])
    path = str(acc / "eSUN PLA Matte Black.json")
    r = core.handle({"action": "import", "paths": [path], "target": "user"})
    assert r["results"][0]["ok"]
    with open(orca / "user" / "default" / "filament" / "eSUN PLA Matte Black.json", encoding="utf-8") as fh:
        assert json.load(fh)["filament_vendor"] == ["eSUN"]
    # and it is not reported as changed on the next scan
    item = next(i for i in core.handle({"action": "scan"})["items"] if i["path"] == path)
    assert item["status"] == "imported"

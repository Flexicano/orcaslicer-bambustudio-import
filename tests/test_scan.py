import glob
import json
import os

import pytest

import bambustudio_import as bi


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))


def test_scan_kinds_and_base(tmp_path):
    write(str(tmp_path / "filament" / "a.json"), {"name": "a"})
    write(str(tmp_path / "filament" / "base" / "b.json"), {"name": "b"})
    write(str(tmp_path / "process" / "p.json"), {"name": "p"})
    write(str(tmp_path / "machine" / "m.json"), {"name": "m"})
    write(str(tmp_path / "filament" / "a.info"), "x = 1")
    items = bi.scan_account(str(tmp_path))
    assert [i.kind for i in items] == ["filament", "filament", "machine", "process"]
    assert len(items) == 4


def test_scan_uses_json_name_not_filename(tmp_path):
    write(str(tmp_path / "filament" / "old.json"), {"name": "New Name", "inherits": "Generic PLA"})
    [item] = bi.scan_account(str(tmp_path))
    assert item.name == "New Name"
    assert item.inherits == "Generic PLA"
    assert item.data["name"] == "New Name"


def test_scan_invalid_json(tmp_path):
    write(str(tmp_path / "filament" / "bad.json"), '{"name":')
    [item] = bi.scan_account(str(tmp_path))
    assert item.data is None
    assert item.error
    assert item.name == "bad"
    assert item.inherits == ""


def test_scan_non_object_json(tmp_path):
    write(str(tmp_path / "process" / "list.json"), "[1, 2]")
    [item] = bi.scan_account(str(tmp_path))
    assert item.data is None
    assert item.error == "not a JSON object"


REAL = os.path.expanduser("~/Library/Application Support/BambuStudio")


@pytest.mark.skipif(not os.path.isdir(os.path.join(REAL, "user")), reason="no BambuStudio data")
def test_scan_real_profiles():
    account = bi.bambu_accounts(REAL)[0][0]
    acc = os.path.join(REAL, "user", account)
    items = bi.scan_account(acc)
    files = [f for k in bi.KINDS for f in glob.glob(os.path.join(acc, k, "**", "*.json"), recursive=True)]
    assert len(items) == len(files) > 0
    assert all(i.data is not None for i in items)


def test_read_preset_with_utf8_bom(tmp_path):
    # Windows editors such as Notepad may save JSON with a byte order mark
    path = tmp_path / "PLA.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"name": "PLA", "inherits": "Generic PLA"}).encode("utf-8"))
    p = bi._read_preset("filament", str(path))
    assert p.data is not None and p.name == "PLA" and p.inherits == "Generic PLA"

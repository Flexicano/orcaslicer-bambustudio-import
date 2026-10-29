import json
import os

import pytest

import bambustudio_import as bi
from helpers import FakeBundles, FakeHost


@pytest.fixture
def bdir(tmp_path):
    return str(tmp_path / "user" / "acc" / "_local" / "bambustudio_import")


def test_safe_filename():
    assert bi.safe_filename('a/b:c*?"<>|\\d') == "a_b_c_______d"
    assert bi.safe_filename("PLA+ Wit") == "PLA+ Wit"


def test_ensure_metadata(bdir):
    bi.ensure_metadata(bdir)
    with open(os.path.join(bdir, "bundle_metadata.json"), encoding="utf-8") as fh:
        assert json.load(fh) == {"id": "bambustudio_import", "name": "BambuStudio import", "version": "1.0.0",
                                 "description": "Profielen geïmporteerd uit BambuStudio",
                                 "author": "BambuStudio Import plugin"}


def test_write_and_list_roundtrip(bdir):
    bi.write_preset(bdir, "filament", {"name": "PLA+ Wit — mat"})
    bi.write_preset(bdir, "process", {"name": "0.20mm a/b"})
    names = bi.imported_names(bdir)
    assert names == {"filament": {"PLA+ Wit — mat"}, "process": {"0.20mm a/b"}, "machine": set()}


def test_imported_names_missing_dir(tmp_path):
    assert bi.imported_names(str(tmp_path / "nope")) == {"filament": set(), "process": set(), "machine": set()}


def test_rewrite_overwrites_no_tmp_left(bdir):
    bi.write_preset(bdir, "filament", {"name": "X", "v": 1})
    path = bi.write_preset(bdir, "filament", {"name": "X", "v": 2})
    files = os.listdir(os.path.join(bdir, "filament"))
    assert files == ["X.json"]
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["v"] == 2


def test_clear_only_touches_bundle(bdir):
    bi.ensure_metadata(bdir)
    bi.write_preset(bdir, "filament", {"name": "X"})
    notes = os.path.join(bdir, "notes.txt")
    open(notes, "w").close()
    other = os.path.join(os.path.dirname(bdir), "other", "x.json")
    os.makedirs(os.path.dirname(other))
    open(other, "w").close()
    bi.clear_bundle(bdir)
    assert not os.path.exists(os.path.join(bdir, "filament"))
    # metadata stays so Orca still lists the (now empty) bundle and can reload it
    assert os.path.exists(os.path.join(bdir, "bundle_metadata.json"))
    assert os.path.exists(notes) and os.path.exists(other)


def test_clear_refuses_wrong_dir(tmp_path):
    with pytest.raises(ValueError):
        bi.clear_bundle(str(tmp_path / "user" / "acc"))
    with pytest.raises(ValueError):
        bi.clear_bundle(str(tmp_path / "elsewhere" / "bambustudio_import"))


def test_reload_missing_api():
    assert bi.reload_bundle(FakeHost()) == (False, "Herstart OrcaSlicer om de profielen te laden")


def test_reload_not_listed():
    b = FakeBundles(ids=["other"])
    assert bi.reload_bundle(FakeHost(bundles=b)) == (False, "OrcaSlicer ziet de bundle nog niet — herstart OrcaSlicer")
    assert b.reloaded == []


def test_reload_runtime_error():
    b = FakeBundles(ids=["bambustudio_import"], error="bad json")
    assert bi.reload_bundle(FakeHost(bundles=b)) == (False, "Herladen mislukt: bad json")


def test_reload_success_without_list_ids():
    b = FakeBundles(has_list=False)
    assert bi.reload_bundle(FakeHost(bundles=b)) == (True, "Profielen geladen")
    assert b.reloaded == ["bambustudio_import"]


def test_colliding_filenames_do_not_overwrite(bdir):
    p1 = bi.write_preset(bdir, "filament", {"name": "PLA A/B"})
    p2 = bi.write_preset(bdir, "filament", {"name": "PLA A_B"})
    p3 = bi.write_preset(bdir, "filament", {"name": "pla a_b"})
    assert len({p1.lower(), p2.lower(), p3.lower()}) == 3
    assert bi.imported_names(bdir)["filament"] == {"PLA A/B", "PLA A_B", "pla a_b"}
    # re-import of the same name still overwrites its own file
    assert bi.write_preset(bdir, "filament", {"name": "PLA A_B", "v": 2}) == p2
    assert len(os.listdir(os.path.join(bdir, "filament"))) == 3


def test_clear_does_not_use_scandir(bdir, monkeypatch):
    # Orca's audit hook prompts for os.scandir without naming a path
    bi.ensure_metadata(bdir)
    bi.write_preset(bdir, "filament", {"name": "X"})

    def boom(*a, **k):
        raise AssertionError("os.scandir used")
    monkeypatch.setattr(os, "scandir", boom)
    bi.clear_bundle(bdir)
    assert not os.path.exists(os.path.join(bdir, "filament"))

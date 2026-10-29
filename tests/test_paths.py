import os

import bambustudio_import as bi


def mk(path):
    os.makedirs(path, exist_ok=True)
    return path


def touch(path, text="{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def test_orca_data_dir_walks_up(tmp_path):
    here = mk(str(tmp_path / "OrcaSlicer" / "orca_plugins" / "bambustudio_import"))
    assert bi.orca_data_dir(here) == str(tmp_path / "OrcaSlicer")


def test_bambu_accounts_sorted_by_count(tmp_path):
    user = tmp_path / "user"
    for i in range(3):
        touch(str(user / "111" / "filament" / f"f{i}.json"))
    touch(str(user / "default" / "process" / "p.json"))
    mk(str(user / "junk"))
    assert bi.bambu_accounts(str(tmp_path)) == [("111", 3), ("default", 1)]


def test_preset_folder_from_file():
    assert bi.preset_folder_from_file("/x/OrcaSlicer/user/5744/filament/x.json") == "5744"
    assert bi.preset_folder_from_file("/a/b.json") is None


def test_resolve_preset_folder_fallbacks(tmp_path):
    a = tmp_path / "a"
    mk(str(a / "user" / "default"))
    mk(str(a / "user" / "abc"))
    assert bi.resolve_preset_folder([], str(a)) == "abc"
    b = tmp_path / "b"
    for n in ("default", "x", "y"):
        mk(str(b / "user" / n))
    assert bi.resolve_preset_folder([], str(b)) == "default"


def test_resolve_preset_folder_prefers_user_file(tmp_path):
    assert bi.resolve_preset_folder(["/o/user/zzz/filament/a.json"], str(tmp_path)) == "zzz"


def test_bundle_dir():
    assert bi.bundle_dir("/orca", "abc") == os.path.join("/orca", "user", "abc", "_local", "bambustudio_import")


def test_bambu_dirs_linux_includes_flatpak(tmp_path, monkeypatch):
    monkeypatch.setattr(bi.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    native = mk(str(tmp_path / ".config" / "BambuStudio"))
    flatpak = mk(str(tmp_path / ".var" / "app" / "com.bambulab.BambuStudio" / "config" / "BambuStudio"))
    assert bi.bambu_data_dirs(str(tmp_path)) == [native, flatpak]


def test_bambu_dirs_windows_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(bi.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    d = mk(str(tmp_path / "Roaming" / "BambuStudio"))
    assert bi.bambu_data_dirs(str(tmp_path)) == [d]


def test_source_labels_are_unique():
    labels = [bi.source_label(d) for d in (
        "/h/.config/BambuStudio", "/h/.config/BambuStudioBeta",
        "/h/.var/app/com.bambulab.BambuStudio/config/BambuStudio")]
    assert labels == ["BambuStudio", "BambuStudioBeta", "BambuStudio (Flatpak)"]


def test_preset_folder_ignores_home_named_user():
    orca = "/home/user/.config/OrcaSlicer"
    f = orca + "/user/5744/filament/x.json"
    assert bi.preset_folder_from_file(f, orca) == "5744"
    # without orca_dir: the 'user' segment that is followed by a kind folder
    assert bi.preset_folder_from_file(f) == "5744"
    assert bi.preset_folder_from_file(orca + "/user/5744/_local/b/filament/x.json") == "5744"


def test_preset_folder_windows_path():
    f = r"C:\Users\alice\AppData\Roaming\OrcaSlicer\user\5744\process\p.json"
    assert bi.preset_folder_from_file(f) == "5744"


def test_bambu_dirs_linux_honours_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(bi.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg = mk(str(tmp_path / "xdg" / "BambuStudio"))
    native = mk(str(tmp_path / ".config" / "BambuStudio"))
    # ~/.config is still searched: inside OrcaSlicer's Flatpak XDG_CONFIG_HOME points at
    # Orca's own sandbox folder, not at where BambuStudio keeps its profiles
    assert bi.bambu_data_dirs(str(tmp_path)) == [xdg, native]


def test_bambu_dirs_linux_xdg_default_not_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(bi.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    native = mk(str(tmp_path / ".config" / "BambuStudio"))
    assert bi.bambu_data_dirs(str(tmp_path)) == [native]


def test_safe_filename_valid_on_windows():
    # Windows drops trailing dots and spaces from file names
    assert bi.safe_filename("PLA. ") == "PLA__"
    assert bi.safe_filename('a/b:c*?"<>|') == "a_b_c______"

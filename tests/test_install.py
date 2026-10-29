import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import install  # noqa: E402


def test_default_orca_dir_per_platform():
    assert install.default_orca_dir("darwin", "/h", {}) == "/h/Library/Application Support/OrcaSlicer"
    assert install.default_orca_dir("linux", "/h", {}) == "/h/.config/OrcaSlicer"
    assert install.default_orca_dir("win32", "C:/h", {"APPDATA": "C:/h/AppData/Roaming"}) == \
        os.path.join("C:/h/AppData/Roaming", "OrcaSlicer")


def test_linux_flatpak_orca_when_native_missing(tmp_path):
    flat = tmp_path / ".var" / "app" / "com.orcaslicer.OrcaSlicer" / "config" / "OrcaSlicer"
    flat.mkdir(parents=True)
    assert install.default_orca_dir("linux", str(tmp_path), {}) == str(flat)


def test_linux_old_flatpak_id_still_found(tmp_path):
    flat = tmp_path / ".var" / "app" / "io.github.softfever.OrcaSlicer" / "config" / "OrcaSlicer"
    flat.mkdir(parents=True)
    assert install.default_orca_dir("linux", str(tmp_path), {}) == str(flat)


def test_linux_honours_xdg_config_home(tmp_path):
    assert install.default_orca_dir("linux", "/h", {"XDG_CONFIG_HOME": "/x"}) == "/x/OrcaSlicer"


def test_install_copies_only_plugin_file(tmp_path):
    orca = tmp_path / "OrcaSlicer"
    dest = install.install(str(orca))
    assert dest == str(orca / "orca_plugins" / "bambustudio_import" / "bambustudio_import.py")
    assert os.path.isfile(dest)
    assert os.listdir(orca / "orca_plugins" / "bambustudio_import") == ["bambustudio_import.py"]

"""Install the BambuStudio Import plugin into OrcaSlicer (macOS, Windows, Linux).

    python3 scripts/install.py                 # OrcaSlicer's default data folder
    python3 scripts/install.py <orca data dir> # somewhere else (portable install)

Only creates/overwrites orca_plugins/bambustudio_import/bambustudio_import.py.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "..", "orca_plugins", "bambustudio_import", "bambustudio_import.py")
FLATPAK_ORCA = os.path.join(".var", "app", "io.github.softfever.OrcaSlicer", "config", "OrcaSlicer")


def default_orca_dir(platform, home, env):
    if platform == "win32":
        return os.path.join(env.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "OrcaSlicer")
    if platform == "darwin":
        return home + "/Library/Application Support/OrcaSlicer"
    native = home + "/.config/OrcaSlicer"
    flatpak = os.path.join(home, FLATPAK_ORCA)
    if not os.path.isdir(native) and os.path.isdir(flatpak):
        return flatpak
    return native


def install(orca_dir):
    dest_dir = os.path.join(orca_dir, "orca_plugins", "bambustudio_import")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "bambustudio_import.py")
    shutil.copyfile(SOURCE, dest)
    return dest


def main(argv):
    orca_dir = argv[1] if len(argv) > 1 else default_orca_dir(sys.platform, os.path.expanduser("~"), os.environ)
    print("Geïnstalleerd: %s" % install(orca_dir))
    print("Herstart OrcaSlicer en zet de plugin aan via File > Plugins.")


if __name__ == "__main__":
    main(sys.argv)

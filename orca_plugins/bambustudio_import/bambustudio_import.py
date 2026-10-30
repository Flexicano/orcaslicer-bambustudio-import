# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "BambuStudio Import"
# description = "Pick your own BambuStudio filament, process and printer profiles and import them into OrcaSlicer, as a preset bundle that loads without a restart or as your own user profiles."
# author = "ronniebaks.nl"
# version = "0.6.1"
# ///
"""BambuStudio Import for OrcaSlicer.

A Pages plugin (a tab in the top bar) that lists the user profiles of
BambuStudio, shows per profile whether it can work in OrcaSlicer, and copies
the ticked ones into a local preset bundle:

    <data_dir>/user/<preset_folder>/_local/bambustudio_import/

followed by ``orca.host.preset.bundles.reload_local`` so they show up without
a restart. BambuStudio's files are only ever read. The plugin API has no way
to create presets, which is why it goes through a local bundle.

Everything except ``ImportPage`` and the plugin class is plain Python without
``orca``, so the offline tests can import this file.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field

try:
    import orca
except ImportError:          # offline tests
    orca = None

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE_ID = "bambustudio_import"
KINDS = ("filament", "process", "machine")


def _log(msg):
    print("[bambustudio_import] %s" % msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Languages: English, plus Dutch when OrcaSlicer runs in Dutch
# ---------------------------------------------------------------------------

STRINGS = {
    "en": {
        "ready": "Ready to import",
        "missing_parent": "Parent '{inherits}' does not exist in OrcaSlicer",
        "imported": "Already imported — tick to overwrite",
        "exists": "Name already exists in OrcaSlicer — importing adds a second preset with this name",
        "outdated": "Changed in BambuStudio since import — tick to update",
        "invalid": "Invalid JSON: {err}",
        "imported_ok": "Imported",
        "write_failed": "Could not write: {err}",
        "not_found_bambu": "Not found in BambuStudio",
        "nothing_imported": "Nothing imported",
        "nothing_to_clear": "Nothing to clear",
        "profile_not_found": "Profile not found",
        "unknown_action": "Unknown action: {action}",
        "starting_up": "OrcaSlicer is still starting up, try again",
        "bambu_not_found": "BambuStudio not found in {path}",
        "restart_to_load": "Restart OrcaSlicer to load the profiles",
        "not_listed": "OrcaSlicer doesn't see the bundle yet — restart OrcaSlicer",
        "reload_failed": "Reload failed: {err}",
        "loaded": "Profiles loaded",
        "saved_user": "{n} saved as user profile — restart OrcaSlicer to see them",
        "imported_user": "Imported as user profile",
        "removed": "Removed",
        "not_imported": "Not imported",
        "removed_user": "{n} user profile(s) removed — restart OrcaSlicer to finish",
        "removed_pending": "Removed — restart OrcaSlicer to finish",
        "restored": "Previous version restored",
        "restored_user": "Previous version restored — restart OrcaSlicer to see it",
        "no_backup": "No previous version saved",
        "file_source": "File · {name}",
        "file_loaded": "{n} profile(s) read from {name}",
        "file_empty": "No BambuStudio profiles found in {name}",
        "file_bad": "Could not read {name}: {err}",
        "file_too_big": "{name} is too large",
        "file_forgotten": "File forgotten",
        "not_a_file": "Not an imported file",
    },
    "nl": {
        "ready": "Klaar om te importeren",
        "missing_parent": "Parent '{inherits}' bestaat niet in OrcaSlicer",
        "imported": "Al geïmporteerd — aanvinken overschrijft",
        "exists": "Naam bestaat al in OrcaSlicer — import maakt een tweede preset met deze naam",
        "outdated": "Gewijzigd in BambuStudio sinds de import — aanvinken werkt bij",
        "invalid": "Ongeldige JSON: {err}",
        "imported_ok": "Geïmporteerd",
        "write_failed": "Schrijven mislukt: {err}",
        "not_found_bambu": "Niet gevonden in BambuStudio",
        "nothing_imported": "Niets geïmporteerd",
        "nothing_to_clear": "Niets om leeg te maken",
        "profile_not_found": "Profiel niet gevonden",
        "unknown_action": "Onbekende actie: {action}",
        "starting_up": "OrcaSlicer is nog aan het opstarten, probeer opnieuw",
        "bambu_not_found": "BambuStudio niet gevonden in {path}",
        "restart_to_load": "Herstart OrcaSlicer om de profielen te laden",
        "not_listed": "OrcaSlicer ziet de bundle nog niet — herstart OrcaSlicer",
        "reload_failed": "Herladen mislukt: {err}",
        "loaded": "Profielen geladen",
        "saved_user": "{n} als gebruikersprofiel opgeslagen — herstart OrcaSlicer om ze te zien",
        "imported_user": "Geïmporteerd als gebruikersprofiel",
        "removed": "Verwijderd",
        "not_imported": "Niet geïmporteerd",
        "removed_user": "{n} gebruikersprofiel verwijderd — herstart OrcaSlicer om het af te ronden",
        "removed_pending": "Verwijderd — herstart OrcaSlicer om het af te ronden",
        "restored": "Vorige versie teruggezet",
        "restored_user": "Vorige versie teruggezet — herstart OrcaSlicer om hem te zien",
        "no_backup": "Geen vorige versie bewaard",
        "file_source": "Bestand · {name}",
        "file_loaded": "{n} profiel(en) gelezen uit {name}",
        "file_empty": "Geen BambuStudio-profielen gevonden in {name}",
        "file_bad": "Kon {name} niet lezen: {err}",
        "file_too_big": "{name} is te groot",
        "file_forgotten": "Bestand vergeten",
        "not_a_file": "Geen geïmporteerd bestand",
    },
}


def lang_from(code):
    """OrcaSlicer's UI language code (``nl_NL``) -> "nl" or "en"."""
    return "nl" if str(code or "").lower().startswith("nl") else "en"


def tr(lang, key, **kw):
    table = STRINGS.get(lang) or STRINGS["en"]
    return (table.get(key) or STRINGS["en"][key]).format(**kw)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _app_support(app, home=None):
    home = home or os.path.expanduser("~")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")), app)
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", app)
    return os.path.join(home, ".config", app)


def orca_data_dir(here=HERE):
    """OrcaSlicer's data directory: the parent of the ``orca_plugins`` folder
    this file was installed under, else the OS default."""
    p = here
    for _ in range(6):
        if os.path.basename(p).lower() == "orca_plugins":
            return os.path.dirname(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return _app_support("OrcaSlicer")


FLATPAK_BAMBU = os.path.join(".var", "app", "com.bambulab.BambuStudio", "config", "BambuStudio")


def bambu_data_dirs(home=None):
    """Existing BambuStudio data directories: release, beta and, on Linux,
    $XDG_CONFIG_HOME and the Flatpak install (which keeps its config inside
    ~/.var/app). ~/.config is searched even when XDG_CONFIG_HOME is set:
    inside OrcaSlicer's Flatpak it points at Orca's own sandbox folder."""
    apps = ("BambuStudio", "BambuStudioBeta")
    dirs = []
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            dirs += [os.path.join(xdg, app) for app in apps]
    dirs += [_app_support(app, home) for app in apps]
    if sys.platform.startswith("linux"):
        dirs.append(os.path.join(home or os.path.expanduser("~"), FLATPAK_BAMBU))
    out = []
    for d in dirs:
        if os.path.isdir(d) and os.path.normpath(d) not in map(os.path.normpath, out):
            out.append(d)
    return out


def source_label(bambu_dir):
    """Name shown for a BambuStudio data directory in the account picker."""
    norm = bambu_dir.replace("\\", "/")
    if "/.var/app/" in norm:
        return "%s (Flatpak)" % os.path.basename(norm)
    return os.path.basename(norm)


def _count_json(path):
    n = 0
    for _root, _dirs, files in os.walk(path):
        n += sum(1 for f in files if f.lower().endswith(".json"))
    return n


def bambu_accounts(bambu_dir):
    """``(account, json_count)`` for each user folder holding profiles,
    most profiles first."""
    user = os.path.join(bambu_dir, "user")
    try:
        names = os.listdir(user)
    except OSError:
        return []
    found = []
    for name in names:
        acc = os.path.join(user, name)
        kind_dirs = [os.path.join(acc, k) for k in KINDS if os.path.isdir(os.path.join(acc, k))]
        if kind_dirs:
            found.append((name, sum(_count_json(d) for d in kind_dirs)))
    return sorted(found, key=lambda t: (-t[1], t[0]))


def preset_folder_from_file(path, orca_dir=None):
    """Account folder of a preset file under ``<orca>/user/<folder>/...``.

    With ``orca_dir`` the path is taken relative to it. Without, the ``user``
    segment that is followed by ``<folder>/<kind or _local>`` is used, so a
    home folder called ``user`` (``/home/user/...``) is not mistaken for it.
    """
    norm = (path or "").replace("\\", "/")
    if orca_dir:
        root = orca_dir.replace("\\", "/").rstrip("/") + "/user/"
        if norm.startswith(root):
            rest = norm[len(root):].split("/")
            return rest[0] if len(rest) > 1 and rest[0] else None
    parts = norm.split("/")
    for idx in range(len(parts) - 3, -1, -1):
        if parts[idx] == "user" and parts[idx + 2] in KINDS + ("_local",):
            return parts[idx + 1]
    return None


def resolve_preset_folder(user_preset_files, orca_dir):
    """The active Orca preset folder: taken from a live user preset's file,
    else the only signed-in account folder, else ``default``."""
    for f in user_preset_files:
        folder = preset_folder_from_file(f, orca_dir)
        if folder:
            return folder
    try:
        accounts = [n for n in os.listdir(os.path.join(orca_dir, "user"))
                    if n != "default" and os.path.isdir(os.path.join(orca_dir, "user", n))]
    except OSError:
        accounts = []
    return accounts[0] if len(accounts) == 1 else "default"


def bundle_dir(orca_dir, preset_folder):
    return os.path.join(orca_dir, "user", preset_folder, "_local", BUNDLE_ID)


def user_dir(orca_dir, preset_folder):
    """Where Orca keeps the account's own user presets (``<kind>/*.json``)."""
    return os.path.join(orca_dir, "user", preset_folder)


def plugin_data_dir(orca_dir):
    """The plugin's own folder (renames, backups, loaded export files). Orca
    does not read it, so nothing in it ever shows up as a preset."""
    return os.path.join(orca_dir, "bambustudio_import")


def folder_data_dir(orca_dir, preset_folder):
    return os.path.join(plugin_data_dir(orca_dir), "accounts", safe_filename(preset_folder))


def files_dir(orca_dir):
    return os.path.join(plugin_data_dir(orca_dir), "files")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

# (folder relative to the account, kind)
SOURCE_DIRS = (
    ("filament", "filament"),
    (os.path.join("filament", "base"), "filament"),
    ("process", "process"),
    ("machine", "machine"),
)


@dataclass
class SourcePreset:
    kind: str           # "filament" | "process" | "machine"
    name: str           # "name" from the JSON, else the file stem
    path: str
    inherits: str       # "" for a base profile
    data: dict | None   # None when the file could not be parsed
    error: str


def _read_preset(kind, path):
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, encoding="utf-8-sig") as fh:  # -sig: tolerate a BOM (Notepad)
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        return SourcePreset(kind, stem, path, "", None, str(e) or type(e).__name__)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        name = stem
    inherits = data.get("inherits")
    return SourcePreset(kind, name, path, inherits if isinstance(inherits, str) else "", data, "")


def scan_account(account_dir):
    """All BambuStudio user profiles of one account folder."""
    items = []
    for rel, kind in SOURCE_DIRS:
        folder = os.path.join(account_dir, rel)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for fname in names:
            path = os.path.join(folder, fname)
            if fname.lower().endswith(".json") and os.path.isfile(path):
                items.append(_read_preset(kind, path))
    items.sort(key=lambda i: (i.kind, i.name.lower()))
    return items


# ---------------------------------------------------------------------------
# Display metadata (material, colour, printer) for the tab
# ---------------------------------------------------------------------------

# Longest names first so "PETG" wins over "PET" and "PLA-CF" over "PLA".
MATERIALS = ("PETG", "PCTG", "PLA", "ABS", "ASA", "TPU", "PET", "PA", "PC", "PVA", "HIPS", "PP", "PE")
_MATERIAL_RE = re.compile(r"(?<![A-Z0-9])(%s)(?![A-Z])" % "|".join(MATERIALS))
PRINTERS = ("A1 mini", "A1", "P1S", "P1P", "P2S", "X1C", "X1E", "X1", "H2D", "H2S", "H2C")
_PRINTER_RE = re.compile(r"\b(%s)\b" % "|".join(re.escape(p) for p in PRINTERS), re.I)
_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def _first(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    return value if isinstance(value, str) else ""


def material_of(name, inherits, data):
    """Filament material: ``filament_type`` when present, else read from the
    preset name or its parent ("Generic PLA Silk" -> PLA). "" when unknown."""
    ftype = _first((data or {}).get("filament_type")).strip().upper()
    if ftype:
        return ftype
    m = _MATERIAL_RE.search(("%s %s" % (name, inherits)).upper())
    return m.group(1) if m else ""


def colour_of(data):
    """Filament colour as #rrggbb, or "" (anything else is dropped, it ends up
    in the page's style attribute)."""
    data = data or {}
    for key in ("default_filament_colour", "filament_colour"):
        c = _first(data.get(key)).strip()
        if _COLOUR_RE.match(c):
            return c
    return ""


# Words that start a preset name without being a brand.
NOT_VENDORS = frozenset({"generic", "pla", "petg", "abs", "asa", "tpu", "pet", "pa", "pc", "pva", "hips",
                         "my", "mine", "custom", "default", "test", "new", "copy"})
VENDOR_ALIASES = {"bambu": "Bambu Lab", "bbl": "Bambu Lab"}


def vendor_of(name, data):
    """Filament brand: ``filament_vendor`` when set, else the first word of
    the preset name when that looks like a brand. "" for generic/unknown."""
    vendor = _first((data or {}).get("filament_vendor")).strip()
    if not vendor:
        first = (name or "").split(" ", 1)[0].strip()
        if not first or first[0].isdigit() or first.casefold() in NOT_VENDORS:
            return ""
        vendor = first
    if vendor.casefold() in ("generic", "") :
        return ""
    return VENDOR_ALIASES.get(vendor.casefold(), vendor)


def canonical_vendors(vendors):
    """``{casefolded: most common spelling}`` so eSUN/eSun end up as one brand."""
    counts = {}
    for v in vendors:
        if v:
            counts.setdefault(v.casefold(), {}).setdefault(v, 0)
            counts[v.casefold()][v] += 1
    return {k: max(spell.items(), key=lambda kv: (kv[1], kv[0]))[0] for k, spell in counts.items()}


def printer_of(name, inherits, data):
    """Printer model a preset targets (A1, P2S, ...), "" when generic."""
    text = " ".join([name, inherits] + [p for p in (data or {}).get("compatible_printers") or [] if isinstance(p, str)])
    m = _PRINTER_RE.search(text)
    if not m:
        return ""
    found = m.group(1)
    return next(p for p in PRINTERS if p.lower() == found.lower())


# ---------------------------------------------------------------------------
# What Orca already has
# ---------------------------------------------------------------------------

# preset_bundle collection -> kind
HOST_COLLECTIONS = (("filaments", "filament"), ("prints", "process"), ("printers", "machine"))


def _collection_presets(collection):
    """Presets of a PresetCollection binding, which offers size()/preset(i)
    and is not iterable."""
    if collection is None:
        return []
    try:
        return [collection.preset(i) for i in range(collection.size())]
    except Exception as e:  # noqa: BLE001
        _log("cannot read preset collection: %r" % e)
        return []


def _printer_list(value):
    """compatible_printers as a list of names (the binding may hand back a
    list, a ConfigOption or a ;-separated string)."""
    value = getattr(value, "value", value)
    if isinstance(value, str):
        value = [v for v in value.replace(";", "\n").splitlines()]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip().strip('"') for v in value if str(v).strip().strip('"')]


@dataclass
class OrcaIndex:
    names: dict         # kind -> set of preset names
    keys: dict          # kind -> {preset name: set of config keys}
    user_files: list    # file paths of Orca user presets
    compat: dict = field(default_factory=dict)   # kind -> {preset name: [printer names]}
    current_printer: str = ""                    # printer preset selected in Orca
    presets: dict = field(default_factory=dict)  # kind -> {preset name: host preset}, for values

    def value(self, kind, name, key):
        """A setting of an Orca preset as a list of strings ([] if unknown)."""
        p = self.presets.get(kind, {}).get(name)
        if p is None:
            return []
        try:
            v = getattr(p.config_value(key), "value", None) or p.config_value(key)
        except Exception:  # noqa: BLE001
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",")] if v else []
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return [] if v is None else [str(v)]

    @classmethod
    def from_host(cls, bundle):
        """Snapshot of ``orca.host.preset_bundle()``. UI thread only."""
        names = {k: set() for k in KINDS}
        keys = {k: {} for k in KINDS}
        compat = {k: {} for k in KINDS}
        hosted = {k: {} for k in KINDS}
        user_files = []
        for attr, kind in HOST_COLLECTIONS:
            for p in _collection_presets(getattr(bundle, attr, None)):
                try:
                    name = str(p.name)
                    pkeys = set(p.config_keys())
                    if p.is_user() and p.file:
                        user_files.append(str(p.file))
                    if kind != "machine" and "compatible_printers" in pkeys:
                        compat[kind][name] = _printer_list(p.config_value("compatible_printers"))
                except Exception as e:  # noqa: BLE001 - one bad preset must not hide the rest
                    _log("skipping preset: %r" % e)
                    continue
                names[kind].add(name)
                keys[kind][name] = pkeys
                hosted[kind][name] = p
        current = ""
        try:
            current = str(bundle.printers.get_selected_preset_name() or "")
        except Exception as e:  # noqa: BLE001
            _log("selected printer: %r" % e)
        return cls(names, keys, user_files, compat, current, hosted)

    def union_keys(self, kind):
        out = set()
        for ks in self.keys.get(kind, {}).values():
            out |= ks
        return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Suffix for presets imported under another name because Orca already has the name.
RENAME_SUFFIX = " (Bambu)"


@dataclass
class Classified:
    preset: SourcePreset
    status: str         # ready | missing_parent | exists | imported | outdated | invalid
    reason: str
    selectable: bool
    default_checked: bool


def _parent_resolvable(presets, index):
    """Per preset: can its ``inherits`` chain be satisfied by Orca, possibly
    through other Bambu user presets of the same kind? Fixed point over the
    scanned set, so chains of any length work and cycles stay unresolved."""
    by_key = {}
    for p in presets:
        if p.data is not None:
            by_key.setdefault((p.kind, p.name), p)
    ok = {}
    changed = True
    while changed:
        changed = False
        for key, p in by_key.items():
            if ok.get(key):
                continue
            parent = p.inherits
            good = (not parent or parent in index.names.get(p.kind, ())
                    or ok.get((p.kind, parent), False))
            if good:
                ok[key] = True
                changed = True
    return ok


def classify(presets, index, imported, lang="nl", renames=None):
    ok = _parent_resolvable(presets, index)
    out = []
    for p in presets:
        if p.data is None:
            out.append(Classified(p, "invalid", tr(lang, "invalid", err=p.error), False, False))
        elif not ok.get((p.kind, p.name)):
            out.append(Classified(p, "missing_parent", tr(lang, "missing_parent", inherits=p.inherits), False, False))
        elif imported_target(p.kind, p.name, imported, renames):
            out.append(Classified(p, "imported", tr(lang, "imported"), True, False))
        elif p.name in index.names.get(p.kind, ()):
            out.append(Classified(p, "exists", tr(lang, "exists"), True, False))
        else:
            out.append(Classified(p, "ready", tr(lang, "ready"), True, True))
    return out


def compat_of(preset, index, parents):
    """Printers the preset shows up for in Orca: its own compatible_printers,
    else the nearest parent's (Bambu user parents first, then Orca's).
    [] means every printer."""
    seen = set()
    cur = preset
    while True:
        own = _printer_list((cur.data or {}).get("compatible_printers"))
        if own:
            return own
        parent = cur.inherits
        if not parent or parent in seen:
            return []
        seen.add(parent)
        if parent in index.names.get(preset.kind, ()):
            return list(index.compat.get(preset.kind, {}).get(parent, []))
        cur = parents.get(parent)
        if cur is None:
            return []


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

# BambuStudio bookkeeping that means nothing to Orca.
ALWAYS_DROP = frozenset({"setting_id", "base_id", "user_id", "updated_time", "instantiation"})
SETTINGS_ID_KEY = {"filament": "filament_settings_id", "process": "print_settings_id",
                   "machine": "printer_settings_id"}
ALWAYS_KEEP = frozenset({"name", "inherits", "from", "version"}) | frozenset(SETTINGS_ID_KEY.values())


# Orca options that accept "nil" (the per-filament retraction overrides).
# Everywhere else a "nil" makes Orca reject the whole bundle.
ORCA_NULLABLE_PREFIXES = ("filament_retract", "filament_retraction_", "filament_deretraction_speed",
                          "filament_z_hop", "filament_wipe", "filament_long_retractions_when_cut")


def orca_nullable(key):
    return key.startswith(ORCA_NULLABLE_PREFIXES)


def _ancestor_values(preset, index, parents, key):
    """Values of ``key`` up the inherits chain: Bambu user parents, then Orca."""
    seen = set()
    name = preset.inherits
    while name and name not in seen:
        seen.add(name)
        if name in index.names.get(preset.kind, ()):
            v = index.value(preset.kind, name, key)
            if v:
                yield v
            return
        parent = parents.get(name)
        if parent is None:
            return
        v = (parent.data or {}).get(key)
        if isinstance(v, list) and v:
            yield [str(x) for x in v]
        name = parent.inherits


def _fill_nil(values, ancestors):
    """Replace BambuStudio's "nil" (= same as parent for this extruder
    variant) by a real value: the parent's at that position, else another
    variant's. None when nothing is known (then the key is left out)."""
    out = list(values)
    for anc in ancestors:
        for i, v in enumerate(out):
            if v == "nil":
                cand = anc[i] if i < len(anc) else anc[-1]
                if cand != "nil":
                    out[i] = cand
        if "nil" not in out:
            return out
    known = [v for v in out if v != "nil"]
    if not known:
        return None
    return [v if v != "nil" else known[0] for v in out]


def _orca_root(preset, index, parents):
    """First ancestor that exists in Orca, following Bambu user parents."""
    seen = set()
    name = preset.inherits
    while name and name not in seen:
        if name in index.names.get(preset.kind, ()):
            return name
        seen.add(name)
        parent = parents.get(name)
        name = parent.inherits if parent else ""
    return None


def _bambu_chain(preset, index, parents):
    """The Bambu user presets ``preset`` inherits from, nearest first, up to
    (not including) an Orca preset."""
    chain, seen = [], set()
    name = preset.inherits
    while name and name not in seen and name not in index.names.get(preset.kind, ()):
        seen.add(name)
        parent = parents.get(name)
        if parent is None or parent.data is None:
            break
        chain.append(parent)
        name = parent.inherits
    return chain


def convert(preset, index, parents):
    """Orca preset JSON for ``preset`` plus the keys Orca does not know.

    Orca only lets a preset inherit from a system preset, so settings of
    Bambu user parents are merged in (nearest wins) and the result inherits
    the first Orca preset up the chain, or nothing when the chain starts at
    a Bambu base profile."""
    root = _orca_root(preset, index, parents)
    known = index.keys.get(preset.kind, {}).get(root, set()) if root else index.union_keys(preset.kind)
    merged = {}
    for parent in reversed(_bambu_chain(preset, index, parents)):
        merged.update(parent.data)
    merged.update(preset.data)
    merged["inherits"] = root or ""
    out, dropped = {}, []
    for key, value in merged.items():
        if key in ALWAYS_DROP:
            continue
        if key in ALWAYS_KEEP or key in known:
            out[key] = value
        else:
            dropped.append(key)
    for key, value in list(out.items()):
        if isinstance(value, list) and "nil" in value and not orca_nullable(key):
            filled = _fill_nil(value, _ancestor_values(preset, index, parents, key))
            if filled is None:
                del out[key]
            else:
                out[key] = filled
    # A root filament keeps its filament_id: Orca's AMS list only offers root
    # filaments, one per filament_id (BambuStudio's custom filaments). A child
    # takes its parent's id in Orca, so it gets none.
    fid = merged.get("filament_id")
    if "filament_id" in dropped:
        dropped.remove("filament_id")
    if preset.kind == "filament" and not out["inherits"] and isinstance(fid, str) and fid.strip():
        out["filament_id"] = fid
    out["name"] = preset.name
    out["from"] = "User"
    sid = SETTINGS_ID_KEY[preset.kind]
    out[sid] = [preset.name] if preset.kind == "filament" else preset.name
    return out, sorted(dropped)


# ---------------------------------------------------------------------------
# The local bundle
# ---------------------------------------------------------------------------

BUNDLE_METADATA = {
    "id": BUNDLE_ID,
    "name": "BambuStudio import",
    "version": "1.0.0",
    "description": "Profielen geïmporteerd uit BambuStudio",
    "author": "BambuStudio Import plugin",
}
UNSAFE_CHARS = '/\\:*?"<>|'


def safe_filename(name):
    out = "".join("_" if c in UNSAFE_CHARS else c for c in name)
    # Windows silently drops trailing dots and spaces, which would make two
    # names share a file.
    stripped = out.rstrip(". ")
    return stripped + "_" * (len(out) - len(stripped))


def _write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=4, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def ensure_metadata(bdir):
    path = os.path.join(bdir, "bundle_metadata.json")
    if not os.path.exists(path):
        _write_json_atomic(path, BUNDLE_METADATA)


def _preset_path(folder, name):
    """File for preset ``name``: its own file if it already has one, else
    ``<safe name>.json``, else a hashed variant when another preset already
    owns that file. Compared case-insensitively (APFS/NTFS)."""
    base = safe_filename(name)
    candidates = [base + ".json",
                  "%s [%s].json" % (base, hashlib.sha1(name.encode("utf-8")).hexdigest()[:6])]
    try:
        existing = {f.casefold(): f for f in os.listdir(folder)}
    except OSError:
        existing = {}
    for cand in candidates:
        found = existing.get(cand.casefold())
        if found is None:
            return os.path.join(folder, cand)
        p = _read_preset("", os.path.join(folder, found))
        if p.data is None or p.name == name:
            return os.path.join(folder, found)
    return os.path.join(folder, candidates[-1])


def write_preset(bdir, kind, data):
    path = _preset_path(os.path.join(bdir, kind), data["name"])
    _write_json_atomic(path, data)
    return path


def imported_presets(bdir):
    """``{kind: {name: data}}`` of the presets in our bundle."""
    out = {k: {} for k in KINDS}
    for kind in KINDS:
        folder = os.path.join(bdir, kind)
        try:
            files = os.listdir(folder)
        except OSError:
            continue
        for fname in files:
            if fname.lower().endswith(".json"):
                p = _read_preset(kind, os.path.join(folder, fname))
                if p.data is not None:
                    out[kind][p.name] = p.data
    return out


def imported_names(bdir):
    return {kind: set(by_name) for kind, by_name in imported_presets(bdir).items()}


# Presets imported as plain user presets are remembered here (Orca only reads
# the kind folders, so a file next to them is left alone): {kind: {name:
# {"file": basename, "hash": hash of what we wrote}}}.
USER_MANIFEST = "bambustudio_import.json"


def preset_hash(data):
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_manifest(udir):
    try:
        with open(os.path.join(udir, USER_MANIFEST), encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def imported_user_presets(udir):
    """``{kind: {name: hash}}`` of user presets we wrote that still exist."""
    manifest = _read_manifest(udir)
    out = {k: {} for k in KINDS}
    for kind in KINDS:
        for name, entry in (manifest.get(kind) or {}).items():
            if (isinstance(entry, dict) and not entry.get("removed")
                    and os.path.exists(os.path.join(udir, kind, str(entry.get("file", ""))))):
                out[kind][name] = entry.get("hash", "")
    return out


def removed_user_presets(udir):
    """``{kind: set(names)}`` of user presets we removed (Orca keeps listing
    them until it restarts)."""
    manifest = _read_manifest(udir)
    return {kind: {n for n, e in (manifest.get(kind) or {}).items() if isinstance(e, dict) and e.get("removed")}
            for kind in KINDS}


def write_user_preset(udir, kind, data):
    """Write ``data`` as a plain Orca user preset and record it."""
    path = write_preset(udir, kind, data)
    manifest = _read_manifest(udir)
    manifest.setdefault(kind, {})[data["name"]] = {"file": os.path.basename(path), "hash": preset_hash(data)}
    _write_json_atomic(os.path.join(udir, USER_MANIFEST), manifest)
    return path


def remove_user_preset(udir, kind, name):
    """Remove a user preset we wrote. Only the .json goes: Orca deletes the
    cloud copy of a synced preset whose .info is left behind at its next
    start, before it pulls the cloud list (its own offline-delete path).
    Presets not in our manifest are never touched. Returns True if removed."""
    manifest = _read_manifest(udir)
    entry = (manifest.get(kind) or {}).get(name)
    if not isinstance(entry, dict) or entry.get("removed"):
        return False
    path = os.path.join(udir, kind, os.path.basename(str(entry.get("file", ""))))
    removed = False
    if path.endswith(".json") and os.path.exists(path):
        os.remove(path)
        removed = True
    # Remembered as removed, so the tab can say Orca still lists it until a restart.
    manifest[kind][name] = {"file": entry.get("file", ""), "removed": True}
    _write_json_atomic(os.path.join(udir, USER_MANIFEST), manifest)
    return removed


def remove_user_presets(udir):
    """Remove every user preset we wrote; returns how many."""
    return sum(remove_user_preset(udir, kind, name)
               for kind, names in imported_user_presets(udir).items() for name in list(names))


def remove_bundle_preset(bdir, kind, name):
    """Remove preset ``name`` from our bundle. Returns True if removed."""
    path = _preset_path(os.path.join(bdir, kind), name)
    if not os.path.exists(path):
        return False
    p = _read_preset(kind, path)
    if p.data is None or p.name != name:
        return False
    os.remove(path)
    return True


def bundle_has_presets(bdir):
    return any(os.path.isdir(os.path.join(bdir, kind)) and os.listdir(os.path.join(bdir, kind)) for kind in KINDS)


def _remove_tree(path):
    """Like shutil.rmtree, but via os.listdir: Orca's audit hook prompts for
    os.scandir (which rmtree uses) without being able to show the path."""
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        child = os.path.join(path, name)
        if os.path.isdir(child) and not os.path.islink(child):
            _remove_tree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def clear_bundle(bdir):
    """Remove the imported presets. ``bundle_metadata.json`` stays, so Orca
    still lists the bundle and a reload empties it live. Refuses any folder
    that is not our own bundle, so a bad path can never wipe something else."""
    if os.path.basename(bdir) != BUNDLE_ID or os.path.basename(os.path.dirname(bdir)) != "_local":
        raise ValueError("Geen bambustudio_import-bundle: %s" % bdir)
    for kind in KINDS:
        _remove_tree(os.path.join(bdir, kind))


def reload_bundle(host, lang="nl"):
    """Ask Orca to (re)load the bundle. Returns ``(ok, message)``."""
    bundles = getattr(getattr(host, "preset", None), "bundles", None)
    reload_local = getattr(bundles, "reload_local", None)
    if not callable(reload_local):
        return False, tr(lang, "restart_to_load")
    list_ids = getattr(bundles, "list_ids", None)
    try:
        if callable(list_ids) and BUNDLE_ID not in list_ids():
            return False, tr(lang, "not_listed")
        reload_local(BUNDLE_ID)
    except RuntimeError as e:
        _log("reload_local: %s" % e)
        return False, tr(lang, "reload_failed", err=e)
    return True, tr(lang, "loaded")


def user_preset_data(udir, kind, name):
    """What is now in the user preset we wrote as ``name`` (None if gone)."""
    entry = (_read_manifest(udir).get(kind) or {}).get(name)
    if not isinstance(entry, dict) or entry.get("removed"):
        return None
    return _read_preset(kind, os.path.join(udir, kind, os.path.basename(str(entry.get("file", ""))))).data


# ---------------------------------------------------------------------------
# Renames and backups (kept in the plugin's own folder, per Orca account)
# ---------------------------------------------------------------------------

DEFAULT_PREFIX = "BBS - "


def _read_json_file(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_renames(fdir):
    """``{kind: {bambu name: name in Orca}}`` of presets imported under another name."""
    data = _read_json_file(os.path.join(fdir, "renames.json")) or {}
    return {kind: {str(k): str(v) for k, v in (data.get(kind) or {}).items()} for kind in KINDS}


def record_rename(fdir, kind, source, target):
    """Remember ``source`` as imported under ``target`` (None forgets it)."""
    renames = read_renames(fdir)
    if target and target != source:
        renames[kind][source] = target
    elif source in renames[kind]:
        del renames[kind][source]
    else:
        return
    _write_json_atomic(os.path.join(fdir, "renames.json"), renames)


def imported_target(kind, name, imported, renames):
    """The name Bambu preset ``name`` was imported as, or None."""
    mine = imported.get(kind, ())
    for cand in ((renames or {}).get(kind, {}).get(name), name, name + RENAME_SUFFIX):
        if cand and cand in mine:
            return cand
    return None


def renamed_as(name, mode, prefix):
    if mode == "suffix":
        return name + RENAME_SUFFIX
    if mode == "prefix" and prefix.strip():
        return prefix.lstrip() + name
    return name


def _backup_path(fdir, kind, name):
    return os.path.join(fdir, "backup", kind, safe_filename(name) + ".json")


def save_backup(fdir, kind, name, stored, data):
    _write_json_atomic(_backup_path(fdir, kind, name),
                       {"name": name, "stored": stored, "time": time.strftime("%Y-%m-%d %H:%M"), "data": data})


def read_backup(fdir, kind, name):
    b = _read_json_file(_backup_path(fdir, kind, name))
    if b is None or b.get("name") != name or not isinstance(b.get("data"), dict):
        return None
    return b


def drop_backup(fdir, kind, name):
    path = _backup_path(fdir, kind, name)
    if os.path.exists(path):
        os.remove(path)


def diff_settings(kind, old, new):
    """``[[key, old, new], ...]`` of settings that differ, highlights first."""
    keys = [k for k in set(old) | set(new) if k not in META_KEYS and _fmt(old.get(k)) != _fmt(new.get(k))]
    first = [k for k in HIGHLIGHT_KEYS.get(kind, ()) if k in keys]
    rest = sorted(k for k in keys if k not in first)
    return [[k, _fmt(old.get(k)), _fmt(new.get(k))] for k in first + rest]


# ---------------------------------------------------------------------------
# BambuStudio export files (.bbscfg, .bbsflmt, presets .zip, single .json)
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_MEMBER_BYTES = 5 * 1024 * 1024
MAX_MEMBERS = 2000
EXPORT_ARCHIVES = (".zip", ".bbscfg", ".bbsflmt")
_FOLDER_KIND = {"printer": "machine", "machine": "machine", "filament": "filament", "process": "process"}


def _kind_of_export(member, data, filename):
    """Kind of one preset in an export: its folder (.bbscfg), its own type or
    settings id, a filament bundle (.bbsflmt), else the file's name."""
    parts = member.replace("\\", "/").split("/")
    if len(parts) > 1 and parts[0].lower() in _FOLDER_KIND:
        return _FOLDER_KIND[parts[0].lower()]
    if data.get("type") in KINDS:
        return data["type"]
    ids = [kind for kind, key in SETTINGS_ID_KEY.items() if key in data]
    if len(ids) == 1:
        return ids[0]
    low = filename.lower()
    if low.endswith(".bbsflmt"):
        return "filament"
    for word, kind in (("filament", "filament"), ("process", "process"), ("printer", "machine")):
        if word in low:
            return kind
    return None


def _export_json(member, raw, filename):
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or member.rsplit("/", 1)[-1] == "bundle_structure.json":
        return None
    kind = _kind_of_export(member, data, filename)
    if kind is None:
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        data["name"] = os.path.splitext(member.rsplit("/", 1)[-1])[0]
    return kind, data


def presets_from_export(filename, raw, depth=0):
    """``[(kind, data), ...]`` from an exported file. Read in memory: zip
    member paths are never used as paths on disk."""
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        found = _export_json(filename, raw, filename)
        if found is None and depth == 0:
            raise ValueError("not a BambuStudio export")
        return [found] if found else []
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            raise ValueError("too many files")
        for info in infos:
            if info.file_size > MAX_MEMBER_BYTES:
                continue
            low = info.filename.lower()
            if low.endswith(".json"):
                found = _export_json(info.filename, z.read(info), filename)
                if found:
                    out.append(found)
            elif low.endswith(EXPORT_ARCHIVES) and depth == 0:
                out += presets_from_export(info.filename, z.read(info), depth + 1)
    return out


def decode_upload(data):
    """Bytes of a file the tab read as a data: URL (or plain base64)."""
    text = str(data or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    return base64.b64decode(text, validate=False)


def store_export(orca_dir, filename, presets):
    """Keep the presets of an export as a source folder laid out like a
    BambuStudio account; loading the same file again replaces it."""
    stem = safe_filename(os.path.splitext(os.path.basename(filename))[0]).strip()[:80].lstrip(".") or "import"
    folder = os.path.join(files_dir(orca_dir), stem)
    _remove_tree(folder)
    for kind, data in presets:
        write_preset(folder, kind, data)
    _write_json_atomic(os.path.join(folder, "source.json"),
                       {"file": os.path.basename(filename), "time": time.strftime("%Y-%m-%d %H:%M")})
    return stem


def file_sources(orca_dir):
    """``(stem, original file name, folder)`` of every loaded export file."""
    root = files_dir(orca_dir)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for stem in names:
        folder = os.path.join(root, stem)
        if os.path.isdir(folder):
            info = _read_json_file(os.path.join(folder, "source.json")) or {}
            out.append((stem, str(info.get("file") or stem), folder))
    return out


# ---------------------------------------------------------------------------
# Message handling (everything the tab asks for)
# ---------------------------------------------------------------------------

# Shown first in the details panel, in this order; the rest follows sorted.
HIGHLIGHT_KEYS = {
    "filament": ("filament_type", "filament_vendor", "default_filament_colour", "nozzle_temperature",
                 "nozzle_temperature_initial_layer", "hot_plate_temp", "textured_plate_temp",
                 "filament_flow_ratio", "filament_max_volumetric_speed", "pressure_advance", "compatible_printers"),
    "process": ("layer_height", "initial_layer_print_height", "wall_loops", "sparse_infill_density",
                "sparse_infill_pattern", "outer_wall_speed", "inner_wall_speed", "top_shell_layers",
                "bottom_shell_layers", "seam_position", "ironing_type", "compatible_printers"),
    "machine": ("printer_model", "nozzle_diameter", "printable_height", "retraction_length", "retraction_speed"),
}
META_KEYS = frozenset({"name", "from", "version"}) | frozenset(SETTINGS_ID_KEY.values())


def _parents_by_kind(presets):
    parents = {}
    for pr in presets:
        if pr.data is not None:
            parents.setdefault(pr.kind, {}).setdefault(pr.name, pr)
    return parents


def _with_name(kind, data, name):
    """``data`` as preset ``name`` (name and settings id follow)."""
    out = dict(data)
    out["name"] = name
    out[SETTINGS_ID_KEY[kind]] = [name] if kind == "filament" else name
    return out


def _fmt(v):
    """A setting as text; lists joined with ", ", a missing one as ""."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _display_settings(kind, data):
    """``[[key, value], ...]`` of a converted preset for the details panel:
    highlights first, then everything else."""
    first = [k for k in HIGHLIGHT_KEYS.get(kind, ()) if k in data]
    rest = sorted(k for k in data if k not in first and k not in META_KEYS)
    return [[k, _fmt(data[k])] for k in first + rest if _fmt(data[k]).strip(", ")]


class HostNotReady(Exception):
    pass


class ImportCore:
    """Answers the tab's messages. ``host`` is ``orca.host`` (or a fake in the
    tests). Runs on the UI thread, like every ``orca.host`` call must."""

    def __init__(self, host, orca_dir, bambu_dirs, lang="nl"):
        self.host = host
        self.orca_dir = orca_dir
        self.bambu_dirs = list(bambu_dirs)
        self.lang = lang

    def t(self, key, **kw):
        return tr(self.lang, key, **kw)

    # -- helpers -----------------------------------------------------------

    def _accounts(self):
        out = []
        for bdir in self.bambu_dirs:
            app = source_label(bdir)
            for name, count in bambu_accounts(bdir):
                out.append({"id": "%s/%s" % (app, name), "label": "%s · %s" % (app, name), "count": count,
                            "_path": os.path.join(bdir, "user", name)})
        for stem, filename, folder in file_sources(self.orca_dir):
            out.append({"id": "file/%s" % stem, "label": self.t("file_source", name=filename),
                        "count": sum(_count_json(os.path.join(folder, k)) for k in KINDS), "_path": folder})
        return out

    def _index(self):
        try:
            return OrcaIndex.from_host(self.host.preset_bundle())
        except RuntimeError as e:
            _log("preset_bundle: %s" % e)
            raise HostNotReady() from e

    def _state(self, account_id):
        accounts = self._accounts()
        acc = next((a for a in accounts if a["id"] == account_id), accounts[0] if accounts else None)
        index = self._index()
        folder = resolve_preset_folder(index.user_files, self.orca_dir)
        bdir = bundle_dir(self.orca_dir, folder)
        udir = user_dir(self.orca_dir, folder)
        presets = scan_account(acc["_path"]) if acc else []
        in_bundle = imported_presets(bdir)
        in_user = imported_user_presets(udir)
        imported = {kind: set(in_bundle[kind]) | set(in_user[kind]) for kind in KINDS}
        self._fdir = folder_data_dir(self.orca_dir, folder)
        self._renames = read_renames(self._fdir)
        self._imported = imported
        self._in_bundle = in_bundle
        classified = classify(presets, index, imported, self.lang, self._renames)
        parents = _parents_by_kind(presets)
        for i, c in enumerate(classified):
            if c.status != "imported":
                continue
            kind, name = c.preset.kind, c.preset.name
            target = self._target(kind, name)
            expected, _ = convert(c.preset, index, parents.get(kind, {}))
            expected = _with_name(kind, expected, target)
            if target in in_user[kind]:
                # Compare with what we wrote, so edits made in Orca don't count.
                changed = preset_hash(expected) != in_user[kind][target]
            else:
                changed = expected != in_bundle[kind].get(target)
            if changed:
                classified[i] = Classified(c.preset, "outdated", self.t("outdated"), True, True)
        gone = removed_user_presets(udir)
        for i, c in enumerate(classified):
            kind, name = c.preset.kind, c.preset.name
            if c.status == "exists" and name in gone[kind]:
                classified[i] = Classified(c.preset, "ready", self.t("removed_pending"), True, False)
        self._stored = {kind: dict([(n, "bundle") for n in in_bundle[kind]] + [(n, "user") for n in in_user[kind]])
                        for kind in KINDS}
        self._udir = udir
        return accounts, acc, index, bdir, presets, imported, classified

    def _target(self, kind, name):
        """The name Bambu preset ``name`` lives under in Orca, or None."""
        return imported_target(kind, name, self._imported, self._renames)

    def _stored_as(self, kind, name):
        """``"bundle"``/``"user"`` when ``name`` (or its renamed form) is imported, else ``""``."""
        target = self._target(kind, name)
        return self._stored.get(kind, {}).get(target, "") if target else ""

    def _stored_data(self, kind, target):
        """What Orca now has for imported preset ``target`` (None if nothing)."""
        if self._stored.get(kind, {}).get(target) == "user":
            return user_preset_data(self._udir, kind, target)
        return self._in_bundle.get(kind, {}).get(target)

    def _write_stored(self, stored, bdir, kind, data):
        if stored == "user":
            write_user_preset(self._udir, kind, data)
        else:
            ensure_metadata(bdir)
            write_preset(bdir, kind, data)

    # -- actions -----------------------------------------------------------

    def handle(self, msg):
        action = (msg or {}).get("action")
        try:
            if action == "scan":
                return self._scan(msg)
            if action == "import":
                return self._import(msg)
            if action == "clear":
                return self._clear()
            if action == "remove":
                return self._remove(msg)
            if action == "details":
                return self._details(msg)
            if action == "restore":
                return self._restore(msg)
            if action == "load_file":
                return self._load_file(msg)
            if action == "forget_file":
                return self._forget_file(msg)
            return {"type": "error", "message": self.t("unknown_action", action=action)}
        except HostNotReady:
            return {"type": "error", "message": self.t("starting_up")}
        except Exception as e:  # noqa: BLE001 - the page must always get an answer
            _log("%s failed: %r" % (action, e))
            return {"type": "error", "message": str(e) or type(e).__name__}

    def _scan(self, msg):
        accounts, acc, index, bdir, presets, _imported, classified = self._state(msg.get("account"))
        parents = _parents_by_kind(presets)
        scanned = {(p.kind, p.name) for p in presets if p.data is not None}

        def visibility(c):
            if c.preset.kind == "machine" or c.preset.data is None:
                return [], False
            vis = compat_of(c.preset, index, parents.get(c.preset.kind, {}))
            return vis, bool(vis and index.current_printer and index.current_printer not in vis)

        def bambu_parent(p):
            parent = p.inherits
            if parent and parent not in index.names.get(p.kind, ()) and (p.kind, parent) in scanned:
                return parent
            return ""

        vis = {c.preset.path: visibility(c) for c in classified}
        # Printers a profile can land on: Orca's own plus the scanned Bambu
        # printers (which can be imported along with it).
        machines = index.names.get("machine", set()) | {n for k, n in scanned if k == "machine"}
        no_printer = {path: bool(v[0]) and not any(p in machines for p in v[0]) for path, v in vis.items()}

        def has_backup(c):
            target = self._target(c.preset.kind, c.preset.name)
            return bool(target and read_backup(self._fdir, c.preset.kind, target))

        vendors = {c.preset.path: vendor_of(c.preset.name, c.preset.data)
                   for c in classified if c.preset.kind == "filament"}
        canon = canonical_vendors(vendors.values())
        reply = {
            "type": "scan",
            "accounts": [{k: v for k, v in a.items() if not k.startswith("_")} for a in accounts],
            "account": acc["id"] if acc else None,
            "bundle_dir": bdir,
            "items": [{
                "path": c.preset.path, "kind": c.preset.kind, "name": c.preset.name,
                "inherits": c.preset.inherits, "status": c.status, "reason": c.reason,
                "selectable": c.selectable, "checked": c.default_checked and not no_printer[c.preset.path],
                "material": material_of(c.preset.name, c.preset.inherits, c.preset.data)
                if c.preset.kind == "filament" else "",
                "colour": colour_of(c.preset.data) if c.preset.kind == "filament" else "",
                "printer": printer_of(c.preset.name, c.preset.inherits, c.preset.data),
                "vendor": canon.get(vendors.get(c.preset.path, "").casefold(), ""),
                "visible_on": vis[c.preset.path][0],
                "hidden_on_current": vis[c.preset.path][1],
                "current_printer": index.current_printer,
                "bambu_parent": bambu_parent(c.preset),
                "stored": self._stored_as(c.preset.kind, c.preset.name),
                "no_printer": no_printer[c.preset.path],
                "has_backup": has_backup(c),
            } for c in classified],
        }
        if not accounts:
            reply["message"] = self.t("bambu_not_found",
                                      path=", ".join(self.bambu_dirs) or _app_support("BambuStudio"))
        return reply

    def _import(self, msg):
        selected = list(msg.get("paths") or [])
        mode = msg.get("rename_mode")
        if mode not in ("suffix", "prefix"):
            mode = "suffix" if msg.get("rename_conflicts") else ""
        prefix = str(msg.get("rename_prefix", DEFAULT_PREFIX))
        target = "user" if msg.get("target") == "user" else "bundle"
        _accounts, _acc, index, bdir, presets, imported, classified = self._state(msg.get("account"))
        by_path = {c.preset.path: c for c in classified}

        todo = {p for p in selected if p in by_path and by_path[p].selectable}

        parents = _parents_by_kind(presets)

        # Target name per preset: renamed when Orca already has the name (on
        # request), or when an earlier import already renamed it.
        def target_name(c):
            kind, name = c.preset.kind, c.preset.name
            existing = self._target(kind, name)
            if existing:
                return existing
            return renamed_as(name, mode, prefix) if c.status == "exists" else name
        renamed = {}
        for p in todo:
            c = by_path[p]
            if target_name(c) != c.preset.name:
                renamed[(c.preset.kind, c.preset.name)] = target_name(c)

        results = []
        written = 0
        to_user = 0
        for path in selected:
            c = by_path.get(path)
            if c is None:
                results.append({"name": os.path.splitext(os.path.basename(path))[0], "kind": "", "ok": False,
                                "reason": self.t("not_found_bambu"), "dropped": []})
                continue
            kind = c.preset.kind
            entry = {"name": c.preset.name, "kind": kind, "dropped": []}
            if not c.selectable:
                entry.update(ok=False, reason=c.reason)
            else:
                try:
                    data, dropped = convert(c.preset, index, parents.get(kind, {}))
                    name = renamed.get((kind, c.preset.name), c.preset.name)
                    data = _with_name(kind, data, name)
                    # An imported preset is updated where it already lives, so
                    # it never exists twice (bundle and user) in Orca.
                    stored = self._stored_as(kind, c.preset.name) or target
                    # Keep what an update overwrites, so it can be put back.
                    old = self._stored_data(kind, name) if self._stored_as(kind, c.preset.name) else None
                    if old is not None and old != data:
                        save_backup(self._fdir, kind, name, stored, old)
                    self._write_stored(stored, bdir, kind, data)
                    record_rename(self._fdir, kind, c.preset.name, name)
                except (OSError, ValueError, TypeError) as e:
                    _log("write %s: %r" % (c.preset.name, e))
                    entry.update(ok=False, reason=self.t("write_failed", err=e))
                else:
                    if stored == "user":
                        to_user += 1
                    else:
                        written += 1
                    entry.update(name=name, ok=True, stored=stored, dropped=dropped,
                                 reason=self.t("imported_user" if stored == "user" else "imported_ok"))
            results.append(entry)

        if written:
            ok, message = reload_bundle(self.host, self.lang)
        elif to_user:
            ok, message = True, ""
        else:
            ok, message = False, self.t("nothing_imported")
        if to_user:
            message = " · ".join(m for m in (message if written else "", self.t("saved_user", n=to_user)) if m)
        return {"type": "import", "results": results,
                "reload": {"ok": ok, "message": message, "restart": bool(to_user)}}

    def _details(self, msg):
        _accounts, _acc, index, _bdir, presets, _imported, classified = self._state(msg.get("account"))
        c = next((c for c in classified if c.preset.path == msg.get("path")), None)
        if c is None:
            return {"type": "error", "message": self.t("profile_not_found")}
        kind = c.preset.kind
        settings, dropped, changes, backup = [], [], [], None
        if c.selectable:
            data, dropped = convert(c.preset, index, _parents_by_kind(presets).get(kind, {}))
            settings = _display_settings(kind, data)
            target = self._target(kind, c.preset.name)
            if target:
                old = self._stored_data(kind, target)
                if old is not None:
                    changes = diff_settings(kind, old, _with_name(kind, data, target))
                b = read_backup(self._fdir, kind, target)
                backup = b["time"] if b else None
        return {"type": "details", "path": c.preset.path, "name": c.preset.name, "kind": kind,
                "inherits": c.preset.inherits, "status": c.status, "reason": c.reason,
                "settings": settings, "dropped": dropped, "changes": changes, "backup": backup}

    def _restore(self, msg):
        _accounts, _acc, _index, bdir, _presets, _imported, classified = self._state(msg.get("account"))
        c = next((c for c in classified if c.preset.path == msg.get("path")), None)
        if c is None:
            return {"type": "error", "message": self.t("profile_not_found")}
        kind = c.preset.kind
        target, stored = self._target(kind, c.preset.name), self._stored_as(kind, c.preset.name)
        b = read_backup(self._fdir, kind, target) if target else None
        if b is None or not stored:
            return {"type": "restore", "ok": False, "message": self.t("no_backup"), "restart": False}
        self._write_stored(stored, bdir, kind, b["data"])
        drop_backup(self._fdir, kind, target)
        if stored == "user":
            return {"type": "restore", "ok": True, "message": self.t("restored_user"), "restart": True}
        ok, message = reload_bundle(self.host, self.lang)
        return {"type": "restore", "ok": ok, "message": self.t("restored") if ok else message, "restart": False}

    def _load_file(self, msg):
        name = os.path.basename(str(msg.get("name") or "import").replace("\\", "/")) or "import"
        try:
            raw = decode_upload(msg.get("data"))
        except ValueError as e:
            return {"type": "error", "message": self.t("file_bad", name=name, err=e)}
        if len(raw) > MAX_UPLOAD_BYTES:
            return {"type": "error", "message": self.t("file_too_big", name=name)}
        try:
            presets = presets_from_export(name, raw)
        except (ValueError, zipfile.BadZipFile) as e:
            return {"type": "error", "message": self.t("file_bad", name=name, err=e)}
        if not presets:
            return {"type": "error", "message": self.t("file_empty", name=name)}
        stem = store_export(self.orca_dir, name, presets)
        return {"type": "file", "account": "file/" + stem,
                "message": self.t("file_loaded", n=len(presets), name=name)}

    def _forget_file(self, msg):
        account = str(msg.get("account") or "")
        stems = {s: folder for s, _f, folder in file_sources(self.orca_dir)}
        stem = account[5:] if account.startswith("file/") else None
        if stem not in stems:
            return {"type": "error", "message": self.t("not_a_file")}
        _remove_tree(stems[stem])
        return {"type": "file_forgotten", "message": self.t("file_forgotten")}

    def _finish_removal(self, bundle_removed, user_removed):
        """Reload the bundle when it changed; user presets need a restart."""
        if bundle_removed:
            ok, message = reload_bundle(self.host, self.lang)
        else:
            ok, message = bool(user_removed), ""
        if user_removed:
            message = " · ".join(m for m in (message, self.t("removed_user", n=user_removed)) if m)
        return ok, message

    def _remove(self, msg):
        _accounts, _acc, _index, bdir, _presets, _imported, classified = self._state(msg.get("account"))
        by_path = {c.preset.path: c for c in classified}
        results, in_bundle, in_user = [], 0, 0
        for path in msg.get("paths") or []:
            c = by_path.get(path)
            if c is None:
                continue
            kind, name = c.preset.kind, c.preset.name
            stored = self._stored_as(kind, name)
            entry = {"name": name, "kind": kind, "stored": stored, "dropped": []}
            done = False
            target = self._target(kind, name)
            try:
                if stored == "user":
                    done = remove_user_preset(self._udir, kind, target)
                elif stored == "bundle":
                    done = remove_bundle_preset(bdir, kind, target)
                if done:
                    record_rename(self._fdir, kind, name, None)
                    drop_backup(self._fdir, kind, target)
            except OSError as e:
                _log("remove %s: %r" % (name, e))
                entry.update(ok=False, reason=self.t("write_failed", err=e))
                results.append(entry)
                continue
            if done:
                in_user += stored == "user"
                in_bundle += stored == "bundle"
            entry.update(ok=done, reason=self.t("removed" if done else "not_imported"))
            results.append(entry)
        if in_bundle or in_user:
            ok, message = self._finish_removal(in_bundle, in_user)
        else:
            ok, message = False, self.t("nothing_to_clear")
        return {"type": "remove", "results": results, "reload": {"ok": ok, "message": message, "restart": bool(in_user)}}

    def _clear(self):
        index = self._index()
        folder = resolve_preset_folder(index.user_files, self.orca_dir)
        bdir = bundle_dir(self.orca_dir, folder)
        had_bundle = bundle_has_presets(bdir)
        if had_bundle:
            clear_bundle(bdir)
        user_removed = remove_user_presets(user_dir(self.orca_dir, folder))
        _remove_tree(folder_data_dir(self.orca_dir, folder))   # renames and backups
        if not had_bundle and not user_removed:
            return {"type": "clear", "ok": True, "message": self.t("nothing_to_clear"), "restart": False}
        ok, message = self._finish_removal(had_bundle, user_removed)
        return {"type": "clear", "ok": ok, "message": message, "restart": bool(user_removed)}


def permission_targets(bambu_dirs):
    """Read grants for the BambuStudio folders (the scan lists ``user/``
    itself too). The host only grants a directory tree when the target ends
    in ``/*``."""
    targets = []
    for d in bambu_dirs:
        # One entry per folder, in its own separator style: on Windows the
        # host parses "\" and "/" alike and compares paths case-insensitively.
        t = d.rstrip("\\/") + ("\\*" if "\\" in d else "/*")
        if t not in targets:
            targets.append(t)
    return targets


def parse_message(message):
    if isinstance(message, dict):
        return message
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        data = None
    return data if isinstance(data, dict) else {"action": str(message)}


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------

# Tab bar icon: white line art on Orca's dark top bar (same motif as the logo:
# a stack of profiles, an arrow, a new profile).
TAB_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">
<rect x="1.8" y="3.6" width="7" height="9.4" rx="1.4" fill="none" stroke="#ffffff" stroke-width="1.5"/>
<path d="M3.6 1.8h5.6a1.4 1.4 0 0 1 1.4 1.4v7.6" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round"/>
<path d="M3.9 7h2.9M3.9 9.4h2" stroke="#ffffff" stroke-width="1.3" stroke-linecap="round"/>
<path d="M10.4 14.2h3.4m-1.6-1.8 1.8 1.8-1.8 1.8" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
<rect x="14.6" y="10.6" width="4" height="7.4" rx="1" fill="#ffffff"/>
</svg>
"""
TAB_ICON_NAME = "bambustudio_import_tab_icon.svg"

# Full-colour logo for the page header (the same drawing as the OrcaCloud icon).
LOGO_SVG = (
    '<svg viewBox="0 0 320 320" width="40" height="40" aria-hidden="true">'
    '<defs><linearGradient id="bsiG" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#00ae42"/><stop offset="1" stop-color="#008080"/></linearGradient></defs>'
    '<rect width="320" height="320" rx="70" fill="url(#bsiG)"/>'
    '<rect x="50" y="70" width="104" height="130" rx="14" fill="#fff" fill-opacity=".43"/>'
    '<rect x="38" y="88" width="104" height="130" rx="14" fill="#fff"/>'
    '<g fill="#00a05a"><rect x="54" y="112" width="72" height="9" rx="4"/>'
    '<rect x="54" y="134" width="57.6" height="9" rx="4"/><rect x="54" y="156" width="43.2" height="9" rx="4"/></g>'
    '<rect x="152" y="146" width="52" height="20" rx="8" fill="#fff"/>'
    '<path d="M196 124l38 32-38 32z" fill="#fff"/>'
    '<rect x="228" y="104" width="70" height="104" rx="14" fill="#fff" fill-opacity=".92"/>'
    '<g fill="#008080"><rect x="240" y="128" width="46" height="8" rx="4"/>'
    '<rect x="240" y="148" width="36.8" height="8" rx="4"/><rect x="240" y="168" width="27.6" height="8" rx="4"/></g>'
    '</svg>'
)


def write_tab_icon(folder):
    """Write the tab icon into ``folder`` and return its path ("" on failure).
    Rewritten on every call: a plugin update wipes the plugin folder."""
    path = os.path.join(folder, TAB_ICON_NAME)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(TAB_ICON_SVG)
    except OSError as e:
        _log("tab icon: %r" % e)
        return ""
    return path


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="@@html_lang@@"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BambuStudio import</title>
<style>
:root{
  --bg:var(--orca-bg,#f4f5f7);--fg:var(--orca-fg,#17191c);--card:var(--orca-card,#ffffff);
  --muted:#6a7078;--faint:#9aa0a7;--line:#e4e7ea;--line2:#eef0f2;--soft:#f6f7f9;--hover:#f2f8f6;
  --accent:var(--orca-accent,#009688);--accent-fg:#fff;--accent-soft:rgba(0,150,136,.10);
  --ok:#14804a;--ok-bg:#e3f4ea;--warn:#9a5b00;--warn-bg:#fdf1dc;--bad:#c02b2b;--bad-bg:#fbe7e7;--info:#245f9e;--info-bg:#e5eef9;
  --r:12px;--r-sm:8px;--sh1:0 1px 2px rgba(16,24,40,.05);--sh2:0 8px 28px rgba(16,24,40,.12),0 2px 6px rgba(16,24,40,.06)}
@media (prefers-color-scheme: dark){:root{
  --bg:var(--orca-bg,#18191b);--fg:var(--orca-fg,#e8eaec);--card:var(--orca-card,#222427);
  --muted:#a0a6ad;--faint:#70767d;--line:#33363a;--line2:#2b2e31;--soft:#26292c;--hover:#1f2c2a;--accent-soft:rgba(0,180,160,.14);
  --ok:#5fd394;--ok-bg:#183527;--warn:#f2b660;--warn-bg:#382a14;--bad:#f27b7b;--bad-bg:#3d2020;--info:#86b8ee;--info-bg:#1b2b3e;
  --sh1:none;--sh2:0 10px 30px rgba(0,0,0,.45)}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
button,select,input{font:inherit;color:inherit}
svg.i{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.num{font-variant-numeric:tabular-nums}
.app{max-width:1240px;margin:0 auto;padding:20px 24px 110px}
/* header */
.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.top .logo svg{display:block;width:44px;height:44px;border-radius:12px;box-shadow:var(--sh1)}
.top h1{margin:0;font-size:19px;font-weight:700;letter-spacing:-.01em}
.top p{margin:2px 0 0;color:var(--muted)}
.grow{flex:1}
.field{display:flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm);padding:0 10px;height:34px;box-shadow:var(--sh1)}
.field:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.field input,.field select{border:0;outline:0;background:none;height:100%;min-width:0}
.field input{flex:1}
.field .kbd{font-size:11px;color:var(--faint);border:1px solid var(--line);border-radius:4px;padding:0 5px;line-height:16px}
.iconbtn{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px;background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm);cursor:pointer;box-shadow:var(--sh1)}
.iconbtn:hover{border-color:var(--accent);color:var(--accent)}
#msg{margin:10px 0 18px;color:var(--faint);font-size:12px;overflow-wrap:anywhere}
#msg code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--muted)}
/* stat tiles */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:18px}
.toggle{display:inline-flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted)}
.toggle input{accent-color:var(--accent)}
.toggle[hidden]{display:none}
.target{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 14px;padding:12px 14px;background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm, 10px)}
.target .flabel{font-weight:600;color:var(--fg)}
.target .hint{color:var(--muted);font-size:12px;flex:1 1 240px}
.c-prn{white-space:normal;line-height:1.25}
.nz{display:inline-block;margin-top:2px;font-size:11px;font-weight:700;padding:1px 6px;border-radius:5px;background:var(--soft);border:1px solid var(--line);color:var(--fg);white-space:nowrap}
.stag{display:inline-block;font-size:10.5px;font-weight:600;padding:0 6px;margin-right:6px;border-radius:5px;border:1px solid var(--line);color:var(--muted)}
.warn{color:var(--warn);display:inline-flex;align-items:center;gap:4px}
.warn svg.i{width:12px;height:12px}
.vis{margin-top:8px;font-size:12px;color:var(--muted);display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.warnbox{margin-top:10px;display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border-radius:8px;background:var(--warn-bg);color:var(--warn);font-size:12px;font-weight:500}
.stat{text-align:left;background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;cursor:pointer;box-shadow:var(--sh1);transition:border-color .15s,box-shadow .15s}
.stat:hover{border-color:var(--faint)}
.stat.on{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.stat .lbl{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;font-weight:500}
.stat .lbl i{width:8px;height:8px;border-radius:50%}
.stat b{display:block;font-size:24px;font-weight:700;letter-spacing:-.02em;margin-top:4px}
.stat[data-n="0"] b{color:var(--faint)}
.stat .bar2{height:4px;border-radius:2px;background:var(--line2);margin-top:8px;overflow:hidden}
.stat .bar2 span{display:block;height:100%;border-radius:2px;transition:width .3s}
.fhead{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--muted)}
.fhead .reset{border:0;background:none;color:var(--accent);font-weight:600;cursor:pointer;padding:0}
.fhead .reset[hidden]{display:none}
/* filters */
.filters{display:flex;flex-direction:column;gap:10px;margin-bottom:14px}
.frow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.frow .field{flex:1 1 280px}
.seg{display:inline-flex;background:var(--soft);border:1px solid var(--line);border-radius:var(--r-sm);padding:3px;gap:2px}
.seg button{border:0;background:none;padding:5px 12px;border-radius:6px;cursor:pointer;color:var(--muted);font-weight:500;display:inline-flex;gap:6px;align-items:center}
.seg button.on{background:var(--card);color:var(--fg);box-shadow:var(--sh1)}
.seg button .c{color:var(--faint);font-size:11px}
.flabel{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);min-width:72px}
.pills{display:flex;gap:6px;flex-wrap:wrap}
.pill{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 11px;cursor:pointer;font-size:12px;font-weight:500;color:var(--muted)}
.pill:hover{border-color:var(--faint);color:var(--fg)}
.pill.on{background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
.pill .c{opacity:.7;margin-left:3px}
/* layout */
.main{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:start}
.main.withdetail{grid-template-columns:minmax(0,1fr) 340px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh1);overflow:clip}
/* table */
.thead,.tr{display:grid;grid-template-columns:36px minmax(0,2.6fr) 90px 96px minmax(0,1.6fr) 132px;align-items:center;column-gap:10px;padding:0 14px}
.thead{height:38px;border-bottom:1px solid var(--line);background:var(--soft);font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);position:sticky;top:0;z-index:2}
.thead .sort{cursor:pointer;user-select:none;display:flex;gap:4px;align-items:center}
.thead .sort:hover{color:var(--fg)}
.thead .sort.on{color:var(--accent)}
.ghead{display:flex;align-items:center;gap:8px;padding:9px 14px;font-weight:650;background:var(--card);border-bottom:1px solid var(--line2);cursor:pointer;position:sticky;top:38px;z-index:1}
.ghead .n{color:var(--faint);font-weight:500}
.ghead .chev{transition:transform .15s;color:var(--faint)}
.ghead.closed .chev{transform:rotate(-90deg)}
.tr{min-height:48px;border-bottom:1px solid var(--line2);cursor:pointer;transition:background .1s}
.tr:hover{background:var(--hover)}
.tr.sel{background:var(--accent-soft)}
.tr.off{opacity:.55}
.tr.active{box-shadow:inset 3px 0 0 var(--accent);background:var(--accent-soft)}
.nm .t{transition:color .1s}.tr:hover .nm .t{color:var(--accent)}
.cb{width:16px;height:16px;accent-color:var(--accent);margin:0;cursor:pointer}
.nm{display:flex;align-items:center;gap:10px;min-width:0;padding:7px 0}
.sw{width:14px;height:14px;border-radius:50%;flex:none;border:1px solid rgba(0,0,0,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.25)}
.sw.none{background:repeating-linear-gradient(45deg,var(--line) 0 3px,transparent 3px 6px)}
.kicon{color:var(--faint)}
.nm .t{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nm .s{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cell{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:5px;background:var(--soft);border:1px solid var(--line);color:var(--muted)}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;padding:3px 9px;border-radius:999px;white-space:nowrap}
.badge i{width:6px;height:6px;border-radius:50%;background:currentColor}
.s-ready{color:var(--ok);background:var(--ok-bg)}
.s-imported,.s-exists{color:var(--warn);background:var(--warn-bg)}
.s-outdated{color:var(--info);background:var(--info-bg)}
.s-missing_parent,.s-invalid{color:var(--bad);background:var(--bad-bg)}
.skel{height:48px;border-bottom:1px solid var(--line2);display:flex;align-items:center;gap:12px;padding:0 14px}
.skel span{height:10px;border-radius:5px;background:linear-gradient(90deg,var(--line2),var(--line),var(--line2));background-size:200% 100%;animation:sh 1.2s infinite linear}
@keyframes sh{to{background-position:-200% 0}}
#empty{display:none;text-align:center;padding:56px 24px;color:var(--muted)}
#empty .ico{width:44px;height:44px;margin:0 auto 12px;border-radius:12px;background:var(--soft);display:grid;place-items:center;color:var(--faint)}
#empty h2{margin:0 0 4px;color:var(--fg);font-size:15px}
/* detail */
#detail{display:none;position:sticky;top:16px;max-height:calc(100vh - 140px);overflow:auto}
.main.withdetail #detail{display:block}
.hero{height:56px;background:linear-gradient(135deg,var(--hero,var(--accent)),color-mix(in srgb,var(--hero,var(--accent)) 55%,#000));position:relative}
.hero.light{background:linear-gradient(135deg,var(--hero),color-mix(in srgb,var(--hero) 80%,#888))}
.dh{padding:0 16px 12px;border-bottom:1px solid var(--line2);display:flex;gap:12px;align-items:flex-start}
.dh .avatar{width:44px;height:44px;margin-top:-22px;border-radius:12px;border:3px solid var(--card);background:var(--card);display:grid;place-items:center;flex:none;box-shadow:var(--sh1);color:var(--muted)}
.dh .avatar .sw{width:100%;height:100%;border-radius:9px;margin:0;border:0}
.dh .body{padding-top:10px;min-width:0;flex:1}
.facts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.fact{font-size:11.5px;padding:2px 8px;border-radius:6px;background:var(--soft);border:1px solid var(--line);color:var(--muted)}
.fact b{color:var(--fg);font-weight:600}

.dh h3{margin:0;font-size:14px;font-weight:700;overflow-wrap:anywhere}
.dh .s{color:var(--muted);font-size:12px;margin-top:2px;overflow-wrap:anywhere}
.x{position:absolute;right:10px;top:10px;border:0;background:rgba(0,0,0,.25);color:#fff;border-radius:8px;cursor:pointer;padding:4px;display:grid;place-items:center}
.x:hover{background:rgba(0,0,0,.4)}
.dsec{padding:12px 16px;border-bottom:1px solid var(--line2)}
.dsec:last-child{border-bottom:0}
.dsec h4{margin:0 0 8px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--faint)}
.kv{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:6px 12px;font-size:12px}
.kv dt{color:var(--muted);overflow-wrap:anywhere}
.kv dd{margin:0;font-weight:500;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.drop{display:flex;flex-wrap:wrap;gap:5px}
.drop code{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:var(--warn-bg);color:var(--warn);padding:2px 6px;border-radius:4px}
.more{border:0;background:none;color:var(--accent);cursor:pointer;padding:6px 0 0;font-weight:600;font-size:12px}
/* report */
#report{margin-bottom:14px}
#report details{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh1)}
#report summary{cursor:pointer;padding:11px 14px;font-weight:600;display:flex;gap:8px;align-items:center}
#report .res{display:flex;gap:10px;padding:8px 14px;border-top:1px solid var(--line2)}
#report .ok{color:var(--ok)}#report .no{color:var(--bad)}
#report .sub{color:var(--muted);font-size:12px}
/* action bar + toast */
.bar{position:fixed;left:50%;bottom:20px;transform:translate(-50%,140%);transition:transform .22s cubic-bezier(.2,.8,.2,1);z-index:10;display:flex;align-items:center;gap:14px;background:var(--fg);color:var(--bg);border-radius:14px;padding:8px 8px 8px 18px;box-shadow:var(--sh2);max-width:calc(100% - 32px)}
.bar.show{transform:translate(-50%,0)}
.bar .info{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .info b{font-variant-numeric:tabular-nums}
.bar .ghost{border:0;background:none;color:inherit;opacity:.75;cursor:pointer;padding:8px 10px;border-radius:8px;white-space:nowrap}
.bar .primary{white-space:nowrap}
.bar .ghost:hover{opacity:1;background:rgba(127,127,127,.18)}
.bar .ghost.danger{color:#ff8a80;opacity:1}
.bar .ghost[hidden]{display:none}
.rmbtn{margin-top:10px;display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--card);color:var(--bad);border-radius:8px;padding:6px 10px;font-weight:600;cursor:pointer}
.rmbtn:hover{border-color:var(--bad)}
.primary{display:inline-flex;align-items:center;gap:7px;border:0;border-radius:10px;padding:9px 16px;background:var(--accent);color:var(--accent-fg);font-weight:650;cursor:pointer}
.primary:hover{filter:brightness(1.08)}
.primary:disabled{opacity:.5;cursor:default;filter:none}
#banner{position:fixed;right:20px;top:20px;z-index:11;display:none;align-items:center;gap:10px;max-width:420px;padding:11px 14px;border-radius:10px;box-shadow:var(--sh2);font-weight:600;background:var(--card);border:1px solid var(--line)}
#banner.ok,#banner.bad,#banner.info{display:flex;animation:in .2s ease-out}
#banner.ok{color:var(--ok)}#banner.bad{color:var(--bad)}#banner.info{color:var(--info)}
@keyframes in{from{opacity:0;transform:translateY(-6px)}}
footer{margin-top:22px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;color:var(--faint);font-size:12px}
footer b{color:var(--muted);font-weight:600}
.linkbtn{border:0;background:none;cursor:pointer;padding:4px 0;font-weight:600;color:var(--accent);display:inline-flex;align-items:center;gap:6px}
.linkbtn.danger{color:var(--bad)}
.linkbtn:disabled{opacity:.5;cursor:default}
.linkbtn[hidden],.iconbtn[hidden],#conflictWarn[hidden]{display:none}
.iconbtn.accent{border-color:var(--info);color:var(--info)}
#conflictWarn{color:#ffcc80;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;font-size:12px}
.chg{width:100%;border-collapse:collapse;font-size:12px}
.chg td{padding:4px 6px 4px 0;vertical-align:top;overflow-wrap:anywhere}
.chg td:first-child{color:var(--muted);width:40%}
.chg .old{color:var(--bad);text-decoration:line-through;text-decoration-thickness:1px}
.chg .new{color:var(--ok);font-weight:600}
.chg .none{color:var(--faint);font-style:italic;text-decoration:none}
.dbtns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.dbtns .rmbtn{margin-top:0}
.restbtn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--card);color:var(--info);border-radius:8px;padding:6px 10px;font-weight:600;cursor:pointer}
.restbtn:hover{border-color:var(--info)}
.rename{display:inline-flex;align-items:center;gap:6px;color:var(--muted)}
.rename[hidden]{display:none}
.rename select,.rename input{height:26px;border:1px solid var(--line);border-radius:6px;background:var(--card);padding:0 6px}
.rename input{width:90px}
@media (max-width:1000px){.main.withdetail{grid-template-columns:minmax(0,1fr)}#detail{position:static;max-height:none}}
@media (max-width:760px){.app{padding:16px 12px 110px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .thead,.tr{grid-template-columns:30px minmax(0,1fr) 112px}.thead .c-mat,.thead .c-prn,.thead .c-par,.c-mat,.c-prn,.c-par{display:none}
  #checkReady{display:none}.bar{gap:8px;padding-left:14px}}
</style></head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
 <symbol id="i-spool" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6"/></symbol>
 <symbol id="i-layers" viewBox="0 0 24 24"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></symbol>
 <symbol id="i-printer" viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 17h8M9 8h6v5H9z"/></symbol>
 <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></symbol>
 <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></symbol>
 <symbol id="i-chev" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></symbol>
 <symbol id="i-x" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol>
 <symbol id="i-down" viewBox="0 0 24 24"><path d="M12 4v12M6 11l6 6 6-6M5 20h14"/></symbol>
 <symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></symbol>
 <symbol id="i-alert" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/></symbol>
 <symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></symbol>
 <symbol id="i-undo" viewBox="0 0 24 24"><path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></symbol>
 <symbol id="i-file" viewBox="0 0 24 24"><path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M12 11v6M9 14l3 3 3-3"/></symbol>
 <symbol id="i-sort" viewBox="0 0 24 24"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></symbol>
</svg>
<div class="app">
  <header class="top">
    <span class="logo">%%LOGO%%</span>
    <div><h1>BambuStudio import</h1><p>@@subtitle@@</p></div>
    <span class="grow"></span>
    <label class="field" title="@@account@@"><svg class="i"><use href="#i-printer"/></svg><select id="account"></select></label>
    <button id="forgetFile" class="linkbtn danger" hidden><svg class="i"><use href="#i-x"/></svg>@@forget_file@@</button>
    <button id="loadFile" class="iconbtn" title="@@load_file_title@@"><svg class="i"><use href="#i-file"/></svg>@@load_file@@</button>
    <input type="file" id="fileInput" accept=".bbscfg,.bbsflmt,.zip,.json" hidden>
    <button id="updateOutdated" class="iconbtn accent" hidden></button>
    <button id="rescan" class="iconbtn" title="@@rescan@@"><svg class="i"><use href="#i-refresh"/></svg>@@rescan@@</button>
  </header>
  <p id="msg">@@scanning@@</p>

  <div id="statusChips" class="stats"></div>
  <div id="report"></div>

  <div class="target">
    <span class="flabel">@@target_label@@</span>
    <div class="seg" role="radiogroup">
      <button id="targetBundle" data-target="bundle" class="on">@@target_bundle@@</button>
      <button id="targetUser" data-target="user">@@target_user@@</button>
    </div>
    <span id="targetHint" class="hint">@@target_hint_bundle@@</span>
  </div>

  <div class="filters">
    <div class="fhead"><span id="resultCount" class="num"></span><button id="resetFilters" class="reset" hidden>@@clear_filters@@</button><span class="grow"></span><span id="renameRow" class="rename" hidden><span>@@rename_label@@</span><select id="renameMode"><option value="">@@rename_none@@</option><option value="suffix">@@rename_suffix@@</option><option value="prefix">@@rename_prefix@@</option></select><input id="renamePrefix" value="BBS - " hidden aria-label="@@rename_prefix@@"></span></div>
    <div class="frow">
      <label class="field"><svg class="i"><use href="#i-search"/></svg><input id="search" type="search" placeholder="@@search@@"><span class="kbd">/</span></label>
      <div id="kindFilter" class="seg" role="tablist"></div>
    </div>
    <div class="frow" id="materialRow"><span class="flabel">@@material@@</span><div id="materialFilter" class="pills"></div></div>
    <div class="frow" id="vendorRow"><span class="flabel">@@brand@@</span><div id="vendorFilter" class="pills"></div></div>
    <div class="frow" id="printerRow"><span class="flabel">@@printer@@</span><div id="printerFilter" class="pills"></div></div>
  </div>

  <div id="main" class="main">
    <section class="panel">
      <div class="thead">
        <input type="checkbox" class="cb" id="checkAll" title="@@check_all@@">
        <span class="sort" data-sort="name">@@col_name@@ <svg class="i" style="width:12px;height:12px"><use href="#i-sort"/></svg></span>
        <span class="sort c-mat" data-sort="material">@@material@@</span>
        <span class="sort c-prn" data-sort="printer">@@printer@@</span>
        <span class="sort c-par" data-sort="inherits">@@col_parent@@</span>
        <span class="sort" data-sort="status">@@col_status@@</span>
      </div>
      <div id="list"></div>
      <div id="empty"></div>
    </section>
    <aside id="detail" class="panel"></aside>
  </div>

  <footer>
    <span>@@footer@@</span>
    <button id="clear" class="linkbtn danger"><svg class="i"><use href="#i-trash"/></svg>@@clear_all@@</button>
  </footer>
</div>

<div id="bar" class="bar" role="region" aria-label="Selectie">
  <span class="info" id="selInfo"></span>
  <span id="conflictWarn" hidden></span>
  <button id="checkReady" class="ghost">@@all_ready@@</button>
  <button id="checkNone" class="ghost">@@clear_sel@@</button>
  <button id="removeSel" class="ghost danger" hidden></button>
  <button id="import" class="primary" disabled><svg class="i"><use href="#i-down"/></svg>@@import_0@@</button>
</div>
<div id="banner" role="status"></div>

<script>
var T=%%T%%;
function t(k,v){ return String(T[k]==null?k:T[k]).replace(/\{(\w+)\}/g,function(m,n){return v&&v[n]!=null?v[n]:m;}); }
var S={items:[],checked:{},busy:false,loaded:false,account:null,renameMode:"",renamePrefix:"BBS - ",target:"bundle",statusFilter:"",kind:"",material:"",vendor:"",printer:"",
       sort:"name",dir:1,collapsed:{},detail:null};
var KINDS=[["filament",T.kind_filament,"i-spool"],["process",T.kind_process,"i-layers"],["machine",T.kind_machine,"i-printer"]];
var KIND={};KINDS.forEach(function(k){KIND[k[0]]=k;});
var STATUS=[["ready",T.st_ready,"var(--ok)"],["outdated",T.st_outdated,"var(--info)"],["imported",T.st_imported,"var(--warn)"],["exists",T.st_exists,"var(--warn)"],["missing_parent",T.st_missing_parent,"var(--bad)"],["invalid",T.st_invalid,"var(--bad)"]];
var STATUS_LABEL={},STATUS_ORDER={};STATUS.forEach(function(s,i){STATUS_LABEL[s[0]]=s[1];STATUS_ORDER[s[0]]=i;});
var bannerTimer=null;
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function icon(id,cls){return '<svg class="i'+(cls?" "+cls:"")+'"><use href="#'+id+'"/></svg>';}
function post(msg){ if(window.orca && window.orca.postMessage){ window.orca.postMessage(msg); } }
function setMsg(html){ $("msg").innerHTML=html||""; }
function banner(kind,text){
  var b=$("banner"); clearTimeout(bannerTimer);
  if(!kind){ b.className=""; return; }
  b.className=kind; b.innerHTML=icon(kind==="bad"?"i-alert":"i-check")+"<span>"+esc(text)+"</span>";
  if(kind!=="bad") bannerTimer=setTimeout(function(){b.className="";},5000);
}
function setBusy(b){ S.busy=b; ["rescan","import","clear","checkReady","checkNone","removeSel","loadFile","updateOutdated","forgetFile"].forEach(function(i){$(i).disabled=b;}); if(!b) updateCount(); }
function printerOf(it){ return it.printer||T.generic; }
function materialOf(it){ return it.material||T.other; }
function vendorOf(it){ return it.vendor||T.unknown; }
function filtersActive(){ return !!(S.statusFilter||S.kind||S.material||S.vendor||S.printer||$("search").value); }
function visible(it){
  var q=$("search").value.toLowerCase();
  if(S.statusFilter && it.status!==S.statusFilter) return false;
  if(S.kind && it.kind!==S.kind) return false;
  if(S.material && (it.kind!=="filament" || materialOf(it)!==S.material)) return false;
  if(S.vendor && (it.kind!=="filament" || vendorOf(it)!==S.vendor)) return false;
  if(S.printer && printerOf(it)!==S.printer) return false;
  if(q && (it.name+" "+it.inherits).toLowerCase().indexOf(q)<0) return false;
  return true;
}
function removablePaths(){
  return S.items.filter(function(it){return it.stored && S.checked[it.path] && visible(it);}).map(function(it){return it.path;});
}
function askRemove(paths){
  if(!paths.length || !confirm(t("confirm_remove",{n:paths.length}))) return;
  setBusy(true); banner("info",T.removing); post({action:"remove",account:$("account").value||null,paths:paths});
}
function selectedPaths(){
  return S.items.filter(function(it){return it.selectable && S.checked[it.path] && visible(it);}).map(function(it){return it.path;});
}
function updateCount(){
  var sel=S.items.filter(function(it){return it.selectable && S.checked[it.path] && visible(it);});
  var n=sel.length, per={};
  sel.forEach(function(it){per[it.kind]=(per[it.kind]||0)+1;});
  $("import").innerHTML=icon("i-down")+t("import_n",{n:n});
  $("import").disabled=S.busy||n===0;
  $("selInfo").innerHTML=t("selected",{n:"<b>"+n+"</b>"})+(n?" · "+KINDS.filter(function(k){return per[k[0]];}).map(function(k){return per[k[0]]+" "+k[1].toLowerCase();}).join(", "):"");
  $("bar").classList.toggle("show",n>0);
  var conflicts=renameActive()?0:sel.filter(function(it){return it.status==="exists";}).length;
  $("conflictWarn").hidden=conflicts===0;
  $("conflictWarn").innerHTML=icon("i-alert")+esc(t("conflict_warn",{n:conflicts}));
  var rm=removablePaths().length;
  $("removeSel").hidden=rm===0; $("removeSel").disabled=S.busy;
  $("removeSel").innerHTML=icon("i-trash")+esc(t("remove_n",{n:rm}));
  var vis=S.items.filter(function(it){return it.selectable && visible(it);});
  var ca=$("checkAll"); ca.checked=vis.length>0 && vis.every(function(it){return S.checked[it.path];});
  ca.indeterminate=!ca.checked && vis.some(function(it){return S.checked[it.path];});
}
function renameActive(){ return S.renameMode==="suffix" || (S.renameMode==="prefix" && S.renamePrefix.trim()!==""); }
function outdatedPaths(){ return S.items.filter(function(it){return it.status==="outdated";}).map(function(it){return it.path;}); }
function rowNote(it){
  if(it.no_printer) return '<span class="warn">'+icon("i-alert")+esc(T.no_printer_row)+'</span>';
  if(it.hidden_on_current) return '<span class="warn">'+icon("i-alert")+esc(hiddenText(it))+'</span>';
  return esc(it.reason);
}
function counts(key,pred){ var n={}; S.items.forEach(function(it){ if(pred(it)){ var v=key(it); n[v]=(n[v]||0)+1; } }); return n; }
function renderStats(){
  var n=counts(function(it){return it.status;},function(){return true;});
  $("statusChips").innerHTML=STATUS.filter(function(s){return (s[0]!=="invalid"&&s[0]!=="outdated")||n[s[0]];}).map(function(s){
    var c=n[s[0]]||0;
    var pct=S.items.length?Math.round(100*c/S.items.length):0;
    return '<button class="stat'+(S.statusFilter===s[0]?" on":"")+'" data-status="'+s[0]+'" data-n="'+c+'"><span class="lbl"><i style="background:'+s[2]+'"></i>'+esc(s[1])+'</span><b class="num">'+c+'</b><div class="bar2"><span style="width:'+pct+'%;background:'+s[2]+'"></span></div></button>';
  }).join("");
}
function renderFilters(){
  var kn=counts(function(it){return it.kind;},function(){return true;});
  $("kindFilter").innerHTML='<button data-kind=""'+(S.kind===""?' class="on"':"")+'>'+T.all+' <span class="c num">'+S.items.length+'</span></button>'+
    KINDS.filter(function(k){return kn[k[0]];}).map(function(k){return '<button data-kind="'+k[0]+'"'+(S.kind===k[0]?' class="on"':"")+'>'+icon(k[2])+k[1]+' <span class="c num">'+kn[k[0]]+'</span></button>';}).join("");
  var mn=counts(materialOf,function(it){return it.kind==="filament";});
  var mats=Object.keys(mn).sort(function(a,b){return (a===T.other)-(b===T.other)||mn[b]-mn[a]||a.localeCompare(b);});
  $("materialRow").style.display=(mats.length>1 && S.kind!=="process" && S.kind!=="machine")?"":"none";
  if(S.material && !mn[S.material]) S.material="";
  $("materialFilter").innerHTML=pills(mats,mn,S.material,"material");
  var vn=counts(vendorOf,function(it){return it.kind==="filament";});
  var vens=Object.keys(vn).sort(function(a,b){return (a===T.unknown)-(b===T.unknown)||vn[b]-vn[a]||a.localeCompare(b);});
  $("vendorRow").style.display=(vens.length>1 && S.kind!=="process" && S.kind!=="machine")?"":"none";
  if(S.vendor && !vn[S.vendor]) S.vendor="";
  $("vendorFilter").innerHTML=pills(vens,vn,S.vendor,"vendor");
  var pn=counts(printerOf,function(){return true;});
  var prs=Object.keys(pn).sort(function(a,b){return (a===T.generic)-(b===T.generic)||a.localeCompare(b);});
  $("printerRow").style.display=prs.length>1?"":"none";
  if(S.printer && !pn[S.printer]) S.printer="";
  $("printerFilter").innerHTML=pills(prs,pn,S.printer,"printer");
}
function pills(keys,n,cur,attr){
  return '<button class="pill'+(cur===""?" on":"")+'" data-'+attr+'="">'+T.all_pills+'</button>'+keys.map(function(k){
    return '<button class="pill'+(cur===k?" on":"")+'" data-'+attr+'="'+esc(k)+'">'+esc(k)+'<span class="c num">'+n[k]+'</span></button>';}).join("");
}
function cmp(a,b){
  var k=S.sort, x, y;
  if(k==="status"){ x=STATUS_ORDER[a.status]; y=STATUS_ORDER[b.status]; }
  else if(k==="material"){ x=a.material||"~"; y=b.material||"~"; }
  else if(k==="printer"){ x=a.printer||"~"; y=b.printer||"~"; }
  else { x=(a[k]||"").toLowerCase(); y=(b[k]||"").toLowerCase(); }
  return (x<y?-1:x>y?1:a.name.localeCompare(b.name))*S.dir;
}
function swatch(it){
  if(it.kind!=="filament") return icon(KIND[it.kind][2],"kicon");
  return it.colour?'<span class="sw" style="background:'+esc(it.colour)+'"></span>':'<span class="sw none"></span>';
}
function render(){
  var html="", shown=0;
  KINDS.forEach(function(k){
    var rows=S.items.filter(function(it){return it.kind===k[0] && visible(it);}).sort(cmp);
    if(!rows.length) return;
    shown+=rows.length;
    var closed=S.collapsed[k[0]];
    html+='<div class="ghead'+(closed?" closed":"")+'" data-group="'+k[0]+'">'+icon("i-chev","chev")+icon(k[2])+k[1]+' <span class="n num">'+rows.length+'</span></div>';
    if(closed) return;
    rows.forEach(function(it){
      var i=S.items.indexOf(it), on=S.checked[it.path]&&it.selectable;
      html+='<div class="tr'+(it.selectable?"":" off")+(on?" sel":"")+(S.detail===it.path?" active":"")+'" data-i="'+i+'">'+
        '<input type="checkbox" class="cb" data-i="'+i+'"'+(it.selectable?"":" disabled")+(on?" checked":"")+' aria-label="'+esc(t("select_x",{name:it.name}))+'">'+
        '<div class="nm">'+swatch(it)+'<div style="min-width:0"><div class="t" title="'+esc(it.name)+'">'+esc(it.name)+'</div><div class="s">'+storedTag(it)+(it.vendor?esc(it.vendor)+" · ":"")+rowNote(it)+'</div></div></div>'+
        '<span class="cell c-mat">'+(it.material?'<span class="tag">'+esc(it.material)+'</span>':"")+'</span>'+
        '<span class="cell c-prn">'+esc(it.printer||"—")+(nozzleOf(it)?'<br><span class="nz" title="'+esc(t("nozzle_title",{n:nozzleOf(it)}))+'">'+esc(nozzleOf(it))+' mm</span>':"")+'</span>'+
        '<span class="cell c-par" title="'+esc(it.inherits)+'">'+(it.inherits?esc(it.inherits):T.base_profile)+'</span>'+
        '<span><span class="badge s-'+it.status+'"><i></i>'+esc(STATUS_LABEL[it.status]||it.status)+'</span></span></div>';
    });
  });
  $("list").innerHTML=html;
  if(S.loaded && !shown){ S.items.length ? empty(T.nothing_found,T.nothing_found_text) : empty(T.no_profiles,T.no_profiles_text); }
  else empty();
  document.querySelectorAll(".thead .sort").forEach(function(e){ e.classList.toggle("on",e.getAttribute("data-sort")===S.sort); });
  renderStats(); renderFilters(); updateCount();
  $("resultCount").textContent=S.loaded?(filtersActive()?t("count_filtered",{n:shown,total:S.items.length}):t("count_all",{n:S.items.length})):"";
  $("resetFilters").hidden=!filtersActive();
  $("renameRow").hidden=!S.items.some(function(it){return it.status==="exists";});
  $("renamePrefix").hidden=S.renameMode!=="prefix";
  var od=outdatedPaths().length;
  $("updateOutdated").hidden=od===0;
  $("updateOutdated").innerHTML=icon("i-refresh")+esc(t("update_outdated",{n:od}));
  $("forgetFile").hidden=String(S.account||"").indexOf("file/")!==0;
}
function skeleton(){
  var h=""; for(var i=0;i<8;i++){ h+='<div class="skel"><span style="width:16px"></span><span style="width:'+(30+(i*13)%35)+'%"></span><span style="width:60px;margin-left:auto"></span></div>'; }
  $("list").innerHTML=h;
}
function empty(title,text){
  var e=$("empty");
  if(!title){ e.style.display="none"; return; }
  e.innerHTML='<div class="ico">'+icon("i-search")+'</div><h2>'+esc(title)+'</h2><div>'+esc(text||"")+'</div>'; e.style.display="block";
}
function openDetail(path){
  S.detail=path; $("main").classList.add("withdetail");
  $("detail").innerHTML='<div class="dsec"><div class="skel" style="border:0;padding:0"><span style="width:70%"></span></div></div>';
  post({action:"details",account:$("account").value||null,path:path}); render();
}
function closeDetail(){ S.detail=null; $("main").classList.remove("withdetail"); render(); }
function renderDetail(d){
  if(d.path!==S.detail) return;
  var it=S.items.filter(function(x){return x.path===d.path;})[0]||{};
  var col=it.kind==="filament"&&it.colour?it.colour:"";
  var head=col?'<span class="sw" style="background:'+esc(col)+'"></span>':icon(KIND[d.kind][2]);
  var facts=[[T.f_type,KIND[d.kind][1]],[T.material,it.material],[T.brand,it.vendor],[T.printer,it.printer],[T.nozzle,nozzleOf(it)?nozzleOf(it)+" mm":""],[T.stored_as,it.stored?(it.stored==="user"?T.stored_user:T.stored_bundle):""]].filter(function(f){return f[1];});
  var h='<div class="hero'+(col&&isLight(col)?" light":"")+'"'+(col?' style="--hero:'+esc(col)+'"':"")+'><button class="x" id="closeDetail" title="'+esc(T.close)+'">'+icon("i-x")+'</button></div>'+
        '<div class="dh"><span class="avatar">'+head+'</span><div class="body"><h3>'+esc(d.name)+'</h3><div class="s">'+(d.inherits?esc(t("inherits_from",{p:d.inherits})):T.base_profile)+'</div>'+
        '<div class="facts"><span class="badge s-'+d.status+'"><i></i>'+esc(STATUS_LABEL[d.status]||d.status)+'</span>'+facts.map(function(f){return '<span class="fact">'+f[0]+' <b>'+esc(f[1])+'</b></span>';}).join("")+'</div></div></div>';
  h+='<div class="dsec"><div class="s" style="color:var(--muted)">'+esc(d.reason)+'</div>';
  if(it.stored) h+='<div class="dbtns"><button class="rmbtn" id="removeOne">'+icon("i-trash")+esc(T.remove_one)+'</button>'+
    (d.backup?'<button class="restbtn" id="restoreOne" title="'+esc(t("restore_title",{time:d.backup}))+'">'+icon("i-undo")+esc(T.restore_one)+'</button>':"")+'</div>';
  if(it.kind!=="machine"){
    var vis=uniqShort(it.visible_on);
    h+='<div class="vis"><span>'+esc(T.visible_on)+'</span> '+(vis.length?vis.map(function(v){return '<span class="tag">'+esc(v)+'</span>';}).join(" "):'<span class="tag">'+esc(T.all_printers)+'</span>')+'</div>';
    if(it.no_printer) h+='<div class="warnbox">'+icon("i-alert")+'<span>'+esc(T.no_printer_detail)+'</span></div>';
    else if(it.hidden_on_current) h+='<div class="warnbox">'+icon("i-alert")+'<span>'+esc(hiddenText(it))+'</span></div>';
  }
  h+='</div>';
  if(it.stored && d.settings.length){
    var ch=d.changes||[];
    h+='<div class="dsec"><h4>'+esc(t("changes_title",{n:ch.length}))+'</h4>'+(ch.length?'<table class="chg">'+ch.map(function(c){
      return '<tr><td>'+esc(c[0].replace(/_/g," "))+'</td><td><span class="'+(c[1]?"old":"old none")+'">'+esc(c[1]||T.not_set)+'</span> → <span class="'+(c[2]?"new":"new none")+'">'+esc(c[2]||T.not_set)+'</span></td></tr>';
    }).join("")+'</table>':'<div class="s" style="color:var(--muted)">'+esc(T.no_changes)+'</div>')+'</div>';
  }
  if(d.settings.length){
    var first=d.settings.slice(0,10), rest=d.settings.slice(10);
    h+='<div class="dsec"><h4>'+esc(T.after_import)+'</h4><dl class="kv">'+kv(first)+'</dl>';
    if(rest.length) h+='<dl class="kv" id="moreKv" style="display:none;margin-top:6px">'+kv(rest)+'</dl><button class="more" id="moreBtn">'+esc(t("all_settings",{n:d.settings.length}))+'</button>';
    h+='</div>';
  }
  if(d.dropped.length) h+='<div class="dsec"><h4>'+esc(t("dropped",{n:d.dropped.length}))+'</h4><div class="drop">'+d.dropped.map(function(k){return "<code>"+esc(k)+"</code>";}).join("")+'</div></div>';
  $("detail").innerHTML=h;
}
function isLight(hex){ var n=parseInt(hex.slice(1,7),16); return ((n>>16)*299+((n>>8)&255)*587+(n&255)*114)/1000>200; }
function shortPrinter(p){ p=String(p||"").replace(/X1 Carbon/i,"X1C"); var m=/(A1 mini|A1|P1S|P1P|P2S|X1C|X1E|X1|H2D|H2S|H2C)/i.exec(p), n=/(\d+(?:\.\d+)?) ?(?:mm)? nozzle/i.exec(p); return m?m[1].toUpperCase().replace("MINI","mini")+(n?" · "+n[1]+" mm nozzle":""):p; }
function nozzleOf(it){ var s=(it.visible_on||[]).map(function(p){var n=/(\d+(?:\.\d+)?) ?(?:mm)? nozzle/i.exec(p);return n?n[1]:"?";}).filter(function(v,i,a){return a.indexOf(v)===i;}); return s.length===1&&s[0]!=="?"?s[0]:""; }
function storedTag(it){ return it.stored?'<span class="stag">'+esc(it.stored==="user"?T.stored_user:T.stored_bundle)+'</span>':""; }
function setTarget(v){ S.target=v==="user"?"user":"bundle"; $("targetBundle").classList.toggle("on",S.target==="bundle"); $("targetUser").classList.toggle("on",S.target==="user"); $("targetHint").textContent=S.target==="user"?T.target_hint_user:T.target_hint_bundle; try{ localStorage.setItem("bsi_target",S.target); }catch(e){} }
function uniqShort(list){ return (list||[]).map(shortPrinter).filter(function(v,i,arr){return arr.indexOf(v)===i;}); }
function hiddenText(it){ var f=uniqShort(it.visible_on); return t("hidden_current",{"for":f.slice(0,3).join(", ")+(f.length>3?" …":""),cur:shortPrinter(it.current_printer)}); }
function kv(list){ return list.map(function(p){return "<dt>"+esc(p[0].replace(/_/g," "))+"</dt><dd>"+esc(p[1])+"</dd>";}).join(""); }
function renderReport(r){
  var ok=r.results.filter(function(x){return x.ok;}).length, fail=r.results.length-ok;
  var skip=fail?t("skipped",{n:fail}):"";
  if(ok && r.reload.restart) banner(r.reload.ok?"info":"bad", r.reload.message+skip);
  else if(ok && r.reload.ok) banner(fail?"info":"ok", t(ok===1?"imported_loaded_1":"imported_loaded",{n:ok})+skip);
  else if(ok) banner("bad", t("written_not_loaded",{n:ok,msg:r.reload.message}));
  else banner("bad",T.nothing_imported+skip);
  var html='<details'+(fail?" open":"")+'><summary>'+icon(fail?"i-alert":"i-check")+esc(t("last_import",{ok:ok,total:r.results.length}))+'</summary>';
  r.results.forEach(function(x){
    html+='<div class="res"><span class="'+(x.ok?"ok":"no")+'">'+icon(x.ok?"i-check":"i-x")+'</span><div><b>'+esc(x.name)+'</b> <span class="sub">— '+esc(x.reason)+(x.dropped&&x.dropped.length?t("settings_left_out",{n:x.dropped.length}):"")+'</span></div></div>';
  });
  $("report").innerHTML=html+"</details>";
}
function onMessage(d){
  if(typeof d==="string"){ try{ d=JSON.parse(d); }catch(e){ return; } }
  if(!d || typeof d!=="object") return;
  if(d.type==="details"){ renderDetail(d); return; }
  setBusy(false);
  if(d.type==="error"){ banner("bad",d.message); return; }
  if(d.type==="scan"){
    // First scan: tick what is ready. Later scans (after an import, a rescan)
    // keep the user's own ticks, minus what was just imported.
    var prev=S.checked, keep=S.loaded && d.account===S.account; S.checked={};
    S.items=d.items||[];
    S.items.forEach(function(it){
      if(keep ? (prev[it.path] && it.status!=="imported") : it.checked) S.checked[it.path]=true;
    });
    S.loaded=true; S.account=d.account;
    $("account").innerHTML=(d.accounts||[]).map(function(a){return '<option value="'+esc(a.id)+'"'+(a.id===d.account?" selected":"")+'>'+esc(a.label)+' ('+a.count+')</option>';}).join("");
    $("account").parentNode.style.display=(d.accounts||[]).length>1?"":"none";
    if(d.message){ setMsg(""); S.items=[]; render(); empty(T.bambu_not_found,d.message); return; }
    var src=(d.accounts||[]).filter(function(a){return a.id===d.account;})[0];
    var isFile=String(d.account||"").indexOf("file/")===0;
    setMsg(t(isFile?"profiles_in_file":"profiles_target",{n:S.items.length,file:esc(isFile&&src?src.label.replace(/^[^·]*· /,"").replace(/ \(\d+\)$/,""):""),dir:"<code>"+esc(d.bundle_dir)+"</code>"}));
    if(S.detail && !S.items.some(function(it){return it.path===S.detail;})) closeDetail();
    render(); if(S.detail) post({action:"details",account:d.account,path:S.detail}); return;
  }
  if(d.type==="import"){ renderReport(d); scan(); return; }
  if(d.type==="restore"){ banner(d.ok?(d.restart?"info":"ok"):"bad", d.message); scan(); return; }
  if(d.type==="file"){ banner("ok", d.message); closeDetail(); S.loaded=false; scan(d.account); return; }
  if(d.type==="file_forgotten"){ banner("ok", d.message); closeDetail(); S.loaded=false; scan(null); return; }
  if(d.type==="clear"){ banner(d.ok?(d.restart?"info":"ok"):"bad", d.message||T.bundle_cleared); $("report").innerHTML=""; scan(); return; }
  if(d.type==="remove"){ banner(d.reload.ok?(d.reload.restart?"info":"ok"):"bad", d.reload.message); d.results.forEach(function(x){ if(x.ok) delete S.checked[(S.items.filter(function(it){return it.name===x.name && it.kind===x.kind;})[0]||{}).path]; }); scan(); return; }
}
function scan(acc){ setBusy(true); if(!S.loaded) skeleton(); post({action:"scan",account:acc!==undefined?acc:($("account").value||null)}); }
$("rescan").onclick=function(){ banner(); scan(); };
$("account").onchange=function(){ closeDetail(); scan(); };
$("search").oninput=render;
$("statusChips").onclick=function(e){ var b=e.target.closest(".stat"); if(!b) return; var s=b.getAttribute("data-status"); S.statusFilter=S.statusFilter===s?"":s; render(); };
$("kindFilter").onclick=function(e){ var b=e.target.closest("button"); if(!b) return; S.kind=b.getAttribute("data-kind"); render(); };
$("materialFilter").onclick=function(e){ var b=e.target.closest(".pill"); if(!b) return; S.material=b.getAttribute("data-material"); render(); };
$("vendorFilter").onclick=function(e){ var b=e.target.closest(".pill"); if(!b) return; S.vendor=b.getAttribute("data-vendor"); render(); };
$("renameMode").onchange=function(e){ S.renameMode=e.target.value; try{ localStorage.setItem("bsi_rename",S.renameMode); }catch(x){} render(); };
$("renamePrefix").oninput=function(e){ S.renamePrefix=e.target.value; try{ localStorage.setItem("bsi_prefix",S.renamePrefix); }catch(x){} updateCount(); };
try{ S.renameMode=localStorage.getItem("bsi_rename")||""; var px=localStorage.getItem("bsi_prefix"); if(px!==null) S.renamePrefix=px; }catch(x){}
$("renameMode").value=S.renameMode; $("renamePrefix").value=S.renamePrefix;
$("loadFile").onclick=function(){ $("fileInput").click(); };
$("fileInput").onchange=function(){
  var f=$("fileInput").files[0]; $("fileInput").value="";
  if(!f) return;
  if(f.size>20*1024*1024){ banner("bad",t("file_too_big",{name:f.name})); return; }
  var r=new FileReader();
  r.onload=function(){ post({action:"load_file",name:f.name,data:String(r.result||"")}); };
  r.onerror=function(){ setBusy(false); banner("bad",t("file_too_big",{name:f.name})); };
  setBusy(true); banner("info",T.loading_file); r.readAsDataURL(f);
};
$("forgetFile").onclick=function(){ if(!confirm(T.confirm_forget)) return; setBusy(true); post({action:"forget_file",account:S.account}); };
$("updateOutdated").onclick=function(){
  var paths=outdatedPaths(); if(!paths.length) return;
  setBusy(true); banner("info",T.importing); post({action:"import",account:$("account").value||null,paths:paths,target:S.target});
};
$("targetBundle").onclick=function(){ setTarget("bundle"); };
$("targetUser").onclick=function(){ setTarget("user"); };
try{ setTarget(localStorage.getItem("bsi_target")); }catch(e){ setTarget("bundle"); }
$("resetFilters").onclick=function(){ S.statusFilter=S.kind=S.material=S.vendor=S.printer=""; $("search").value=""; render(); };
$("printerFilter").onclick=function(e){ var b=e.target.closest(".pill"); if(!b) return; S.printer=b.getAttribute("data-printer"); render(); };
document.querySelector(".thead").onclick=function(e){ var s=e.target.closest(".sort"); if(!s) return; var k=s.getAttribute("data-sort"); if(S.sort===k) S.dir=-S.dir; else { S.sort=k; S.dir=1; } render(); };
$("checkAll").onchange=function(e){ var on=e.target.checked; S.items.forEach(function(it){ if(it.selectable && visible(it) && (!on || it.status==="ready")) S.checked[it.path]=on; }); render(); };
$("list").onclick=function(e){
  var g=e.target.closest(".ghead"); if(g){ var k=g.getAttribute("data-group"); S.collapsed[k]=!S.collapsed[k]; render(); return; }
  var r=e.target.closest(".tr"); if(!r) return;
  var it=S.items[+r.getAttribute("data-i")];
  if(e.target.classList.contains("cb")){ S.checked[it.path]=e.target.checked; render(); return; }
  if(S.detail===it.path) closeDetail(); else openDetail(it.path);
};
$("detail").onclick=function(e){
  if(e.target.closest("#closeDetail")) closeDetail();
  if(e.target.closest("#removeOne")){ askRemove([S.detail]); return; }
  if(e.target.closest("#restoreOne")){ if(confirm(T.confirm_restore)){ setBusy(true); post({action:"restore",account:$("account").value||null,path:S.detail}); } return; }
  if(e.target.id==="moreBtn"){ $("moreKv").style.display=""; e.target.remove(); }
};
$("checkReady").onclick=function(){ S.items.forEach(function(it){ if(it.status==="ready" && visible(it)) S.checked[it.path]=true; }); render(); };
$("checkNone").onclick=function(){ S.checked={}; render(); };
$("import").onclick=function(){
  var paths=selectedPaths();
  if(!paths.length) return;
  setBusy(true); banner("info",T.importing); post({action:"import",account:$("account").value||null,paths:paths,rename_mode:S.renameMode,rename_prefix:S.renamePrefix,target:S.target});
};
$("removeSel").onclick=function(){ askRemove(removablePaths()); };
$("clear").onclick=function(){
  if(!confirm(T.confirm_clear)) return;
  setBusy(true); post({action:"clear"});
};
document.addEventListener("keydown",function(e){
  if(e.key==="/" && document.activeElement!==$("search")){ e.preventDefault(); $("search").focus(); }
  else if(e.key==="Escape"){ if(S.detail) closeDetail(); else if(document.activeElement===$("search")){ $("search").value=""; render(); } }
});
if(window.orca && window.orca.onMessage){ window.orca.onMessage(onMessage); }
window.__bsi={onMessage:onMessage};
scan();
</script></body></html>
""".replace("%%LOGO%%", LOGO_SVG)


# Texts of the tab; @@key@@ in the page, T.key in its script.
JS_STRINGS = {'en': {'html_lang': 'en', 'subtitle': 'Pick profiles from BambuStudio and bring them into OrcaSlicer in one go.', 'account': 'BambuStudio account', 'rescan': 'Rescan', 'scanning': 'Scanning…', 'clear_filters': 'Clear filters', 'search': 'Search name or parent…', 'material': 'Material', 'brand': 'Brand', 'printer': 'Printer', 'check_all': 'Tick everything visible and ready', 'col_name': 'Name', 'col_parent': 'Parent', 'col_status': 'Status', 'footer': 'BambuStudio is only read. Imported profiles are in Orca under <b>Bundle presets › BambuStudio import</b> or, as user profile, among your own presets. Select imported profiles to remove them again.', 'clear_bundle': 'Empty bundle', 'all_ready': 'All ready', 'clear_sel': 'Clear', 'import_0': 'Import (0)', 'import_n': 'Import ({n})', 'kind_filament': 'Filament', 'kind_process': 'Process', 'kind_machine': 'Printer', 'st_ready': 'Ready', 'st_outdated': 'Updated', 'st_imported': 'Imported', 'st_exists': 'Name exists', 'st_missing_parent': 'Parent missing', 'st_invalid': 'Invalid', 'generic': 'Generic', 'other': 'Other', 'unknown': 'Unknown', 'selected': '{n} selected', 'all': 'All', 'all_pills': 'All', 'select_x': 'Select {name}', 'base_profile': 'base profile', 'inherits_from': 'inherits from {p}', 'nothing_found': 'Nothing found', 'nothing_found_text': 'No profiles match the filters.', 'no_profiles': 'No profiles', 'no_profiles_text': 'This BambuStudio account has no user profiles.', 'count_filtered': '{n} of {total} profiles', 'count_all': '{n} profiles', 'f_type': 'Type', 'close': 'Close (Esc)', 'after_import': 'After import in OrcaSlicer', 'all_settings': 'All settings ({n})', 'dropped': 'Left out ({n}) — unknown to OrcaSlicer', 'imported_loaded_1': '1 profile imported and loaded', 'imported_loaded': '{n} profiles imported and loaded', 'skipped': ' · {n} skipped', 'written_not_loaded': '{n} written, but loading failed: {msg}', 'nothing_imported': 'Nothing imported', 'last_import': 'Last import — {ok} of {total} succeeded', 'settings_left_out': ' · {n} setting(s) left out', 'bambu_not_found': 'BambuStudio not found', 'profiles_target': '{n} profiles in BambuStudio · target {dir}', 'profiles_in_file': '{n} profiles in {file} · target {dir}', 'bundle_cleared': 'Bundle emptied', 'confirm_clear': 'Remove all profiles imported from BambuStudio (bundle and user profiles) from OrcaSlicer? User profiles are also removed from your Orca account at the next start. BambuStudio itself is not touched.', 'importing': 'Importing…', 'hidden_current': 'Only for {for} — hidden while {cur} is selected', 'visible_on': 'Visible for', 'all_printers': 'all printers', 'target_label': 'Import as', 'target_bundle': 'Bundle', 'target_user': 'User profile', 'target_hint_bundle': 'Loaded right away, grouped under Bundle presets › BambuStudio import. Easy to remove in one go.', 'target_hint_user': 'Saved like a profile you made yourself (synced with your Orca account). Restart OrcaSlicer to see them.', 'saved_user': 'Saved as user profile — restart OrcaSlicer', 'stored_user': 'User profile', 'stored_bundle': 'Bundle', 'stored_as': 'Stored as', 'nozzle': 'Nozzle', 'nozzle_title': 'Made for a {n} mm nozzle', 'remove_n': 'Remove ({n})', 'remove_one': 'Remove from OrcaSlicer', 'removing': 'Removing…', 'confirm_remove': 'Remove {n} imported profile(s) from OrcaSlicer? User profiles are also removed from your Orca account at the next start. BambuStudio itself is not touched.', 'clear_all': 'Remove all imported', 'update_outdated': 'Update all changed ({n})', 'load_file': 'Import file…', 'load_file_title': 'A BambuStudio export: .bbscfg, .bbsflmt, a presets .zip or a single .json', 'loading_file': 'Reading file…', 'file_too_big': '{name} is too large', 'forget_file': 'Forget file', 'confirm_forget': 'Forget this file? Profiles you already imported from it stay in OrcaSlicer.', 'rename_label': 'On name conflict', 'rename_none': 'Second preset, same name', 'rename_suffix': 'Import as “Name (Bambu)”', 'rename_prefix': 'Import with prefix', 'conflict_warn': '{n} with an existing name', 'no_printer_row': 'Fits no printer in OrcaSlicer', 'no_printer_detail': 'None of these printers exist in OrcaSlicer, so this profile would not show up anywhere. Add or import the printer first.', 'changes_title': 'Changes on update ({n})', 'no_changes': 'Same as the version in OrcaSlicer.', 'not_set': 'not set', 'restore_one': 'Restore previous version', 'restore_title': 'Saved {time}, before the last update', 'confirm_restore': 'Put back the version from before the last update?'}, 'nl': {'html_lang': 'nl', 'subtitle': 'Kies profielen uit BambuStudio en zet ze in één keer in OrcaSlicer.', 'account': 'BambuStudio-account', 'rescan': 'Opnieuw scannen', 'scanning': 'Bezig met scannen…', 'clear_filters': 'Filters wissen', 'search': 'Zoek op naam of parent…', 'material': 'Materiaal', 'brand': 'Merk', 'printer': 'Printer', 'check_all': 'Alles zichtbaar en klaar aanvinken', 'col_name': 'Naam', 'col_parent': 'Parent', 'col_status': 'Status', 'footer': 'BambuStudio wordt alleen gelezen. Geïmporteerde profielen staan in Orca onder <b>Bundle presets › BambuStudio import</b> of, als gebruikersprofiel, tussen je eigen presets. Selecteer geïmporteerde profielen om ze weer te verwijderen.', 'clear_bundle': 'Bundle leegmaken', 'all_ready': 'Alles klaar', 'clear_sel': 'Wissen', 'import_0': 'Importeer (0)', 'import_n': 'Importeer ({n})', 'kind_filament': 'Filament', 'kind_process': 'Proces', 'kind_machine': 'Printer', 'st_ready': 'Klaar', 'st_outdated': 'Bijgewerkt', 'st_imported': 'Al geïmporteerd', 'st_exists': 'Naam bestaat al', 'st_missing_parent': 'Parent ontbreekt', 'st_invalid': 'Ongeldig', 'generic': 'Algemeen', 'other': 'Overig', 'unknown': 'Onbekend', 'selected': '{n} geselecteerd', 'all': 'Alles', 'all_pills': 'Alle', 'select_x': 'Selecteer {name}', 'base_profile': 'basisprofiel', 'inherits_from': 'erft van {p}', 'nothing_found': 'Niets gevonden', 'nothing_found_text': 'Geen profielen die aan de filters voldoen.', 'no_profiles': 'Geen profielen', 'no_profiles_text': 'Er staan geen user-profielen in dit BambuStudio-account.', 'count_filtered': '{n} van {total} profielen', 'count_all': '{n} profielen', 'f_type': 'Type', 'close': 'Sluiten (Esc)', 'after_import': 'Na import in OrcaSlicer', 'all_settings': 'Alle instellingen ({n})', 'dropped': 'Weggelaten ({n}) — onbekend in OrcaSlicer', 'imported_loaded_1': '1 profiel geïmporteerd en geladen', 'imported_loaded': '{n} profielen geïmporteerd en geladen', 'skipped': ' · {n} overgeslagen', 'written_not_loaded': '{n} geschreven, maar laden lukte niet: {msg}', 'nothing_imported': 'Niets geïmporteerd', 'last_import': 'Laatste import — {ok} van {total} gelukt', 'settings_left_out': ' · {n} instelling(en) weggelaten', 'bambu_not_found': 'BambuStudio niet gevonden', 'profiles_target': '{n} profielen in BambuStudio · doel {dir}', 'profiles_in_file': '{n} profielen in {file} · doel {dir}', 'bundle_cleared': 'Bundle leeggemaakt', 'confirm_clear': 'Alle uit BambuStudio geïmporteerde profielen (bundle én gebruikersprofielen) uit OrcaSlicer verwijderen? Gebruikersprofielen verdwijnen bij de volgende start ook uit je Orca-account. BambuStudio zelf blijft ongemoeid.', 'importing': 'Importeren…', 'hidden_current': 'Alleen voor {for} — verborgen zolang {cur} geselecteerd is', 'visible_on': 'Zichtbaar bij', 'all_printers': 'alle printers', 'target_label': 'Importeren als', 'target_bundle': 'Bundle', 'target_user': 'Gebruikersprofiel', 'target_hint_bundle': 'Direct geladen, gegroepeerd onder Bundle presets › BambuStudio import. In één keer te verwijderen.', 'target_hint_user': 'Opgeslagen als een profiel dat je zelf maakte (synct met je Orca-account). Herstart OrcaSlicer om ze te zien.', 'saved_user': 'Als gebruikersprofiel opgeslagen — herstart OrcaSlicer', 'stored_user': 'Gebruikersprofiel', 'stored_bundle': 'Bundle', 'stored_as': 'Opgeslagen als', 'nozzle': 'Nozzle', 'nozzle_title': 'Gemaakt voor een {n} mm nozzle', 'remove_n': 'Verwijder ({n})', 'remove_one': 'Verwijderen uit OrcaSlicer', 'removing': 'Verwijderen…', 'confirm_remove': '{n} geïmporteerd(e) profiel(en) uit OrcaSlicer verwijderen? Gebruikersprofielen verdwijnen bij de volgende start ook uit je Orca-account. BambuStudio zelf blijft ongemoeid.', 'clear_all': 'Alle imports verwijderen', 'update_outdated': 'Werk alle gewijzigde bij ({n})', 'load_file': 'Bestand importeren…', 'load_file_title': 'Een BambuStudio-export: .bbscfg, .bbsflmt, een presets-.zip of een losse .json', 'loading_file': 'Bestand lezen…', 'file_too_big': '{name} is te groot', 'forget_file': 'Bestand vergeten', 'confirm_forget': 'Dit bestand vergeten? Profielen die je eruit hebt geïmporteerd blijven in OrcaSlicer.', 'rename_label': 'Bij naamconflict', 'rename_none': 'Tweede preset, zelfde naam', 'rename_suffix': 'Importeer als “Naam (Bambu)”', 'rename_prefix': 'Importeer met voorvoegsel', 'conflict_warn': '{n} met een bestaande naam', 'no_printer_row': 'Past bij geen printer in OrcaSlicer', 'no_printer_detail': 'Geen van deze printers bestaat in OrcaSlicer, dus dit profiel zou nergens verschijnen. Voeg de printer eerst toe of importeer hem.', 'changes_title': 'Wijzigingen bij bijwerken ({n})', 'no_changes': 'Gelijk aan de versie in OrcaSlicer.', 'not_set': 'niet ingesteld', 'restore_one': 'Vorige versie terugzetten', 'restore_title': 'Bewaard op {time}, vóór de laatste update', 'confirm_restore': 'De versie van vóór de laatste update terugzetten?'}}


def page_html(lang):
    """The tab in ``lang`` ("en" or "nl")."""
    table = JS_STRINGS.get(lang) or JS_STRINGS["en"]
    html = PAGE_HTML.replace("%%T%%", json.dumps(table, ensure_ascii=False).replace("</", "<\\/"))
    return re.sub(r"@@(\w+)@@", lambda m: table[m.group(1)], html)


if orca is not None:

    class ImportPage(orca.pages.PagesPluginCapabilityBase):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.core = None

        def get_name(self):
            return "BambuStudio import"

        def _lang(self):
            try:
                return lang_from((orca.host.app_info() or {}).get("language"))
            except Exception as e:  # noqa: BLE001 - fall back to English
                _log("app_info: %r" % e)
                return "en"

        def _core(self):
            if self.core is None:
                self.core = ImportCore(orca.host, orca_data_dir(), bambu_data_dirs(), self._lang())
            return self.core

        def get_ui(self):
            return page_html(self._core().lang)

        def get_icon(self):
            return write_tab_icon(HERE)

        def on_message(self, message):
            reply = self._core().handle(parse_message(message))
            try:
                self.post_message(reply)
            except Exception as e:  # noqa: BLE001
                _log("post_message: %r" % e)

    @orca.plugin
    class BambuStudioImportPlugin(orca.base):
        def register_capabilities(self):
            request = getattr(orca, "request_permissions", None)
            targets = permission_targets(bambu_data_dirs())
            if callable(request) and targets:
                try:
                    request(fs_read=targets)
                except TypeError as e:
                    _log("permission request skipped: %r" % e)
            orca.register_capability(ImportPage)

import bambustudio_import as bi


def sp(name, inherits="", kind="filament", data=True, error=""):
    d = {"name": name, "inherits": inherits} if data else None
    return bi.SourcePreset(kind, name, "/src/%s/%s.json" % (kind, name), inherits, d, error)


def index(filament=(), process=(), machine=(), keys=None, user_files=()):
    names = {"filament": set(filament), "process": set(process), "machine": set(machine)}
    k = {kind: {n: set((keys or {}).get(n, ())) for n in ns} for kind, ns in names.items()}
    return bi.OrcaIndex(names, k, list(user_files))


def no_imports():
    return {k: set() for k in bi.KINDS}


class FakePreset:
    def __init__(self, name, keys=(), file="", user=False, values=None):
        self.name = name
        self.file = file
        self._keys = list(keys) + [k for k in (values or {}) if k not in keys]
        self._user = user
        self._values = dict(values or {})

    def config_value(self, key):
        return self._values.get(key, "")

    def is_user(self):
        return self._user

    def config_keys(self):
        return list(self._keys)


class FakeCollection:
    """Mirrors Orca's PresetCollection binding: size()/preset(i), not iterable."""

    def __init__(self, presets, selected=""):
        self._presets = list(presets)
        self._selected = selected

    def get_selected_preset_name(self):
        return self._selected

    def size(self):
        return len(self._presets)

    def preset(self, i):
        return self._presets[i]


class FakeBundle:
    def __init__(self, filaments=(), prints=(), printers=(), selected_printer=""):
        self.filaments = FakeCollection(filaments)
        self.prints = FakeCollection(prints)
        self.printers = FakeCollection(printers, selected_printer)


class FakeBundles:
    def __init__(self, ids=None, error=None, has_list=True):
        self.ids = ids
        self.error = error
        self.reloaded = []
        if not has_list:
            self.list_ids = None

    def list_ids(self):
        return list(self.ids or [])

    def reload_local(self, bundle_id):
        if self.error:
            raise RuntimeError(self.error)
        self.reloaded.append(bundle_id)


class FakePresetNs:
    def __init__(self, bundles):
        self.bundles = bundles


class FakeHost:
    def __init__(self, bundle=None, bundles=None, not_ready=False):
        self._bundle = bundle or FakeBundle()
        self._not_ready = not_ready
        if bundles is not None:
            self.preset = FakePresetNs(bundles)

    def preset_bundle(self):
        if self._not_ready:
            raise RuntimeError("GUI not ready")
        return self._bundle

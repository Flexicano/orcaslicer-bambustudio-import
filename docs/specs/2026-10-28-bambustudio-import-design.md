# BambuStudio Import — OrcaSlicer plugin

Datum: 2026-10-28
Status: ontwerp, ter review

## Doel

Eigen (user) profielen uit BambuStudio eenmalig, op keuze, overzetten naar
OrcaSlicer, zonder handwerk en zonder Orca te herstarten. Na import leven de
profielen los van BambuStudio: er is geen doorlopende sync en niets wordt
ooit naar BambuStudio teruggeschreven.

### Succescriteria

1. Een tab "BambuStudio import" in OrcaSlicer toont alle user-profielen van
   BambuStudio met per profiel een status.
2. Aangevinkte, importeerbare profielen staan na één klik direct in de
   preset-lijsten van Orca (filament / proces / printer).
3. Profielen die niet kunnen werken in Orca (ontbrekende parent, kapotte
   JSON) zijn niet aan te vinken en tonen de reden.
4. BambuStudio-bestanden worden nooit gewijzigd. In Orca wordt alleen
   geschreven in de eigen bundle-map van de plugin.

### Buiten scope

- Tweerichting- of automatische sync.
- Bambu-systeemprofielen (vendor presets) importeren.
- Ontbrekende parents namaken of "platslaan" (flatten) tot een volledig
  zelfstandig profiel.
- Bambu-cloudprofielen ophalen die niet lokaal op schijf staan.
- Windows/Linux-paden testen (code houdt er wel rekening mee, zie *Paden*).

## Context: wat de plugin-API toelaat

Vastgesteld op OrcaSlicer 2.5.0-dev (nightly) en de upstream-PR's:

- `orca.host.preset_bundle()` levert de live presets **read-only**:
  collecties `filaments`, `prints`, `printers`; per preset `name`,
  `is_user()`, `is_system`, `file`, `config_keys()`, `config_value(key)`.
- Nieuwe presets aanmaken kan niet via de API. De mutable-presets-API
  (PR #16518) wijzigt alleen bestaande presets en slaat niet op.
- **Local bundles** (PR #15424): een map
  `{data_dir}/user/{preset_folder}/_local/{bundle_id}/` met
  `bundle_metadata.json` en submappen `filament/`, `process/`, `machine/`
  wordt door Orca als preset-bron geladen.
  `orca.host.preset.bundles.list_ids()` en
  `orca.host.preset.bundles.reload_local(bundle_id)` laden zo'n bundle live
  opnieuw; fouten geven `RuntimeError` en laten de live bundle ongewijzigd.
- Schrijven binnen `data_dir()` vraagt geen toestemming; lezen buiten
  `data_dir()` (BambuStudio-map) wel → vooraf aanvragen met
  `orca.request_permissions(fs_read=[...])`.

## Architectuur

Eén bestand: `orca_plugins/bambustudio_import/bambustudio_import.py`,
met PEP 723-header (`name = "BambuStudio Import"`, `version = "0.1.0"`),
geen externe dependencies. Opgebouwd uit losse, los testbare delen:

| Unit | Taak | Hangt af van |
|---|---|---|
| `paths` | `orca_data_dir()`, `bambu_data_dir()`, `bambu_accounts()`, `orca_preset_folder(bundle)` | OS, `orca.host` (alleen preset_folder) |
| `scan` | Bambu-userprofielen inlezen → lijst `SourcePreset` | bestandssysteem |
| `classify` | per `SourcePreset` status bepalen tegen een `OrcaIndex` | `scan`-output, `OrcaIndex` |
| `OrcaIndex` | snapshot van Orca-presets: namen per type + config-keys per preset | `orca.host.preset_bundle()` |
| `convert` | `SourcePreset` → Orca-JSON (dict) + lijst weggelaten keys | `OrcaIndex` |
| `bundle` | metadata schrijven, presets atomair schrijven, bundle leegmaken, `reload_local` | bestandssysteem, `orca.host.preset.bundles` |
| `ImportPage` | `PagesPluginCapabilityBase`: HTML-UI + berichtafhandeling | alle bovenstaande |

`scan`, `classify` en `convert` zijn pure functies zonder `orca`-import
en daardoor offline te testen. Alleen `OrcaIndex.from_host()`, `bundle.reload`
en `ImportPage` raken `orca`.

### Datamodel

```python
@dataclass
class SourcePreset:
    kind: str          # "filament" | "process" | "machine"
    name: str          # waarde van "name" in de JSON (fallback: bestandsnaam)
    path: str          # absoluut pad naar .json
    inherits: str      # "" als het een basisprofiel is
    data: dict | None  # None bij parse-fout
    error: str         # parse-fout, anders ""

@dataclass
class Classified:
    preset: SourcePreset
    status: str        # "ready" | "missing_parent" | "exists" | "imported" | "invalid"
    reason: str        # mensleesbare uitleg (NL)
    selectable: bool   # False voor missing_parent en invalid
    default_checked: bool  # alleen True voor "ready"
```

## Paden

- **Orca data_dir**: zoals bestaande plugins, omhoog lopen vanaf de
  pluginmap tot `orca_plugins`, de parent daarvan. Fallback per OS
  (`~/Library/Application Support/OrcaSlicer`, `%APPDATA%\OrcaSlicer`,
  `~/.config/OrcaSlicer`).
- **BambuStudio data_dir**: zelfde OS-conventie met `BambuStudio`.
  Een tweede bron `BambuStudioBeta` wordt ook gescand als die bestaat.
- **Bambu-accounts**: submappen van `<bambu>/user/` die minstens één van
  `filament/`, `process/`, `machine/` bevatten (`default` meegerekend).
  Is er meer dan één, dan kiest de gebruiker in de UI; standaard het account
  met de meeste profielen.
- **Orca preset_folder**: afgeleid van het `file`-pad van een bestaande
  user-preset (segment na `user/`), net als FilamentHub. Geen user-presets →
  het enige niet-`default` account dat in `<orca>/user/` staat; anders
  `default`.
- **Bundle-map**: `<orca>/user/<preset_folder>/_local/bambustudio_import/`.

## Scan

- Bronbestanden: `*.json` in `filament/`, `filament/base/`, `process/`,
  `machine/` van het gekozen Bambu-account. `.info`-bestanden worden
  genegeerd.
- `kind` volgt uit de map (`base` → filament).
- Ongeldige JSON of geen object → `SourcePreset` met `data=None`,
  `error` gevuld.

## Classificatie

Volgorde van regels (eerste die past wint):

1. `data is None` → **invalid**, reden = parse-fout.
2. `inherits` niet leeg, niet aanwezig in `OrcaIndex` voor hetzelfde type,
   én niet de naam van een ander gescand Bambu-userprofiel van hetzelfde type
   dat zelf niet *invalid* of *missing_parent* is (een Bambu-profiel kan
   erven van een ander Bambu-userprofiel, bv. uit `filament/base`) →
   **missing_parent**, reden = "Parent 'X' bestaat niet in OrcaSlicer".
3. Naam staat al in de bundle-map van deze plugin → **imported**
   (aanvinkbaar, standaard uit; aanvinken = overschrijven).
4. Naam bestaat in Orca als user- of systeempreset buiten deze bundle →
   **exists** (aanvinkbaar, standaard uit). Import overschrijft de Orca-preset
   niet; Orca toont dan twee presets met dezelfde naam uit verschillende
   bronnen. De UI waarschuwt hiervoor.
5. Anders → **ready** (standaard aan).

Bij regel 2 voor een profiel dat erft van een ander Bambu-userprofiel:
classificatie gebeurt in twee passes, zodat een kind alleen *ready* is als de
ouder zelf *ready* of *imported* is. Bij import zonder de ouder aangevinkt
toont de UI een waarschuwing en wordt het kind overgeslagen.

## Omzetting

Voor elk geselecteerd profiel:

- Weglaten: alle sleutels die Orca niet kent. "Kent" = de sleutel komt voor
  in `config_keys()` van de Orca-parent (of, zonder parent, in de
  vereniging van `config_keys()` over alle Orca-presets van dat type).
- Altijd behouden: `name`, `inherits`, `from` (gezet op `"User"`),
  `version`, plus voor filament `filament_settings_id`, voor proces
  `print_settings_id`, voor printer `printer_settings_id` (gelijk aan naam).
- Altijd weglaten: `setting_id`, `base_id`, `user_id`, `updated_time`,
  `instantiation` (Bambu-meta).
- Waarden worden ongewijzigd overgenomen (Bambu en Orca gebruiken dezelfde
  stringlijst-notatie).
- Resultaat: `(orca_json: dict, dropped_keys: list[str])`.

## Bundle schrijven

1. `bundle_metadata.json` aanmaken als die ontbreekt:
   `{"id": "bambustudio_import", "name": "BambuStudio import",
   "version": "1.0.0", "description": "Profielen geïmporteerd uit BambuStudio",
   "author": "BambuStudio Import plugin"}`.
2. Elk profiel naar `<bundle>/<kind>/<veilige bestandsnaam>.json`, atomair
   (schrijven naar `.tmp`, dan `os.replace`). Veilige bestandsnaam: tekens
   `/\:*?"<>|` vervangen door `_`.
3. `reload_local("bambustudio_import")` aanroepen op de UI-thread
   (on_message draait daar al). Bij `RuntimeError`: melding met Orca's reden
   tonen; bestanden blijven staan zodat een herstart ze alsnog laadt.
   Als `orca.host.preset.bundles` niet bestaat (oudere build): melding
   "herstart OrcaSlicer om de profielen te laden".
4. **Bundle leegmaken**: verwijdert alleen `<bundle>/filament|process|machine`
   en `bundle_metadata.json` binnen de eigen bundle-map, na bevestiging in de
   UI, gevolgd door `reload_local`.

## UI (Pages-tab)

`get_ui()` geeft één inline HTML-pagina (geen externe bronnen), Orca-thema
via de `--orca-*` CSS-variabelen met fallback-kleuren.

- Kop: bronkeuze (account-dropdown als er >1 is), knop **Opnieuw scannen**.
- Filterbalk: tekstzoek, type (alle/filament/proces/printer), printer
  (afgeleid uit `@...` in de naam of `compatible_printers`).
- Lijst gegroepeerd per type; per rij: vinkje, naam, parent, statusbadge,
  reden. Niet-selecteerbare rijen grijs.
- Knoppen: **Alles klaar aanvinken**, **Niets**, **Importeer (n)**,
  **Bundle leegmaken**.
- Na import: rapportpaneel met per profiel geïmporteerd / overgeslagen en
  weggelaten instellingen (inklapbaar).

Berichten JS → Python (`window.orca.postMessage`):

| action | payload | antwoord |
|---|---|---|
| `scan` | `{account?}` | `{type:"scan", accounts, account, items:[...]}` |
| `import` | `{account, paths:[...]}` | `{type:"import", results:[...], reload:{ok, message}}` |
| `clear` | `{}` | `{type:"clear", ok, message}` |

Python → JS via `self.post_message(dict)`.

## Rechten

In `register_capabilities()`:
`orca.request_permissions(fs_read=[<bambu_data_dir>, <bambu_beta_dir>])`
(alleen bestaande paden). Weigeren → plugin laadt niet (Orca-gedrag).

## Foutafhandeling

- BambuStudio niet gevonden → lege lijst met uitleg en het verwachte pad.
- `orca.host.preset_bundle()` nog niet klaar (`RuntimeError`) → melding
  "Orca is nog aan het opstarten, probeer opnieuw" + knop opnieuw scannen.
- Elke Python-uitzondering in `on_message` wordt gevangen en als
  `{type:"error", message}` naar de pagina gestuurd en gelogd naar stderr met
  prefix `[bambustudio_import]`.

## Testen

- **Offline (pytest)**: `scan`, `classify`, `convert`, bundle-schrijven
  tegen een kopie van de echte BambuStudio-profielen (43 stuks) in een
  tijdelijke map, met een nep-`OrcaIndex`. Gevallen: parent bestaat,
  parent ontbreekt, erven van Bambu-userprofiel (met/zonder ouder
  geselecteerd), kapotte JSON, naamconflict, bestandsnaam met `/`,
  onbekende keys weggelaten, Bambu-meta weggelaten, leegmaken raakt niets
  buiten de bundle.
- **Handmatig in OrcaSlicer nightly**: plugin installeren, tab openen, scan
  klopt met BambuStudio, een paar profielen importeren, controleren dat ze
  zonder herstart in de preset-dropdowns staan en slicen; bundle leegmaken.

# BambuStudio Import voor OrcaSlicer

*[English](README.md) · Nederlands*

![Screenshot](assets/screenshot.png)

Een plugin voor OrcaSlicer 2.5 (nightly met Python-plugins) die je eigen
BambuStudio-profielen (filament, proces, printer) eenmalig naar OrcaSlicer
overzet. Je kiest per profiel met vinkjes wat je meeneemt.

## Wat het doet

- Leest de user-profielen van BambuStudio (en `BambuStudioBeta` als die bestaat).
  BambuStudio wordt nooit aangepast. Gezocht wordt in:
  - macOS: `~/Library/Application Support/BambuStudio/user/<account>/`
  - Windows: `%APPDATA%\BambuStudio\user\<account>\`
  - Linux: `~/.config/BambuStudio/user/<account>/`, en bij de Flatpak-versie
    `~/.var/app/com.bambulab.BambuStudio/config/BambuStudio/user/<account>/`
- Toont per profiel of het in OrcaSlicer kan werken:
  - **Klaar** — de parent (`inherits`) bestaat in OrcaSlicer.
  - **Parent ontbreekt** — OrcaSlicer kent het systeemprofiel niet; niet te importeren.
  - **Naam bestaat al** / **Al geïmporteerd** — standaard uit; aanvinken kan.
  - **Ongeldig** — kapot JSON-bestand.
- Je kiest bovenaan hoe je importeert (**Importeren als**):
  - **Bundle** — naar een eigen *local bundle*
    `OrcaSlicer/user/<account>/_local/bambustudio_import/`, direct geladen
    zonder herstart en in één keer te verwijderen.
  - **Gebruikersprofiel** — als gewone user-preset in
    `OrcaSlicer/user/<account>/<filament|process|machine>/`, net als een profiel
    dat je zelf opslaat (synct mee met je Orca-account). Zichtbaar na een
    herstart van OrcaSlicer.
  Een profiel dat al geïmporteerd is, wordt bijgewerkt waar het al staat, zodat
  het nooit dubbel in OrcaSlicer komt.
- Laat per profiel zien voor welke printer en nozzle het is (bijv. *P2S · 0.2 mm
  nozzle*) en waarschuwt als het bij je huidige printer verborgen is.
- Instellingen die OrcaSlicer niet kent worden weggelaten; het rapport na de
  import laat per profiel zien welke.

Na import staan de profielen los van BambuStudio. Wil je er een aanpassen,
gebruik dan in OrcaSlicer "Opslaan als" om er een eigen preset van te maken.

## Installeren

```sh
python3 scripts/install.py
```

Op Windows: `py scripts\install.py`. Voor een portable OrcaSlicer of een
andere datamap geef je die map mee: `python3 scripts/install.py <map>`.
Handmatig kan ook: kopieer `orca_plugins/bambustudio_import/bambustudio_import.py`
naar `<OrcaSlicer-datamap>/orca_plugins/bambustudio_import/`.

Herstart OrcaSlicer, zet de plugin aan via **File → Plugins** (vinkje bij
"BambuStudio Import") en sta het lezen van de BambuStudio-map toe. Het
tabblad **BambuStudio import** staat daarna in de bovenbalk.

Getest op macOS. Windows en Linux zijn ondersteund in de code en de tests,
maar nog niet in een echte OrcaSlicer geprobeerd. Draait OrcaSlicer zelf als
Flatpak, dan moet die Flatpak de BambuStudio-map mogen lezen
(bijvoorbeeld met Flatseal of `flatpak override --user --filesystem=~/.config/BambuStudio:ro com.orcaslicer.OrcaSlicer`).

## Verwijderen

1. In het tabblad: vink geïmporteerde profielen aan en kies **Verwijder (n)**
   (of **Verwijderen uit OrcaSlicer** in het detailpaneel), of gebruik
   **Alle imports verwijderen**. Dat geldt voor bundle- én gebruikersprofielen.
   Bundle-profielen verdwijnen direct; gebruikersprofielen na een herstart van
   OrcaSlicer, die ze dan ook uit je Orca-account haalt. Eigen presets die je
   niet met deze plugin importeerde, worden nooit aangeraakt.
2. Verwijder de map `OrcaSlicer/orca_plugins/bambustudio_import/`.

## Ontwikkelen

```sh
python3 -m pytest
```

De tests draaien zonder OrcaSlicer; de `orca`-module is optioneel.

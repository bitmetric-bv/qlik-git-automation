<!--
BEHEERD BESTAND — niet in deze repo aanpassen.
Bron: bitmetric-bv/qlik-git-automation/templates/qlik-conventions.md
Wijzigingen worden bij de volgende scan (elk uur) naar alle Qlik-repo's gesynct.
App-specifieke info hoort in CLAUDE.md in de root van de repo.
-->

# Qlik Sense-repo's (Gitoqlok) — werkafspraken

Deze repo is een Git-weergave van een Qlik Cloud-app, geschreven door **Gitoqlok**
(Motio Chrome-extensie) vanuit de Qlik-UI. De app zelf is de bron van waarheid voor
objecten; Git is de versiegeschiedenis. Behandel de bestanden daarom voorzichtig.

## Structuur

| Pad | Inhoud |
|---|---|
| `data_load_script/*.qvs` + `tab_order.txt` | Laadscript, één bestand per tab, in volgorde van `tab_order.txt` |
| `loadScript.qvs` | Het volledige laadscript (alle tabs samengevoegd) |
| `objects/sheets`, `masterobjects`, `measures`, `dimensions`, `variables`, `alternatestates` | Eén JSON per object, bestandsnaam = `qInfo.qId` |
| `objects/appprops.json`, `fields.json`, `glossarylinks.json` | App-eigenschappen en veldmetadata |
| `appcontent/` | Media (logo, sheet-thumbnails) |
| `.github/`, `qlik-release.yml`, `.claude/` | Automatisering — niet door Gitoqlok beheerd |

## Branches en release

```
feature/*  --PR-->  dev  --PR-->  main  (= release, automatisch getagd vX.Y.Z)
```

- Gitoqlok commit vanuit de **dev-app** naar `dev` (of een `feature/*`-branch).
- Een merge `dev -> main` start de release: versie, tag, `CHANGELOG.md` en het
  *Log & Version*-blok in de Changelog-tab van het laadscript worden automatisch gemaakt.
  Pas deze **nooit** handmatig aan en bepaal zelf geen versienummer.
- De **prd-app** wordt alleen bijgewerkt met een Gitoqlok-*pull* van `main`/tag.
  **Vanuit prd wordt nooit gecommit of gepusht.** Prd en dev zijn twee aparte apps met
  eigen app-ID's; commits vanuit prd veroorzaken diff-ruis (andere app-ID in media-URL's,
  andere bookmark-ID's) en back-merges.
- Commit nooit rechtstreeks op `main`. Branch protection is in deze org niet technisch
  afgedwongen, dus dit is een afspraak.
- Commitberichten van Gitoqlok eindigen op tags als `[%_OBJECTS_%%_SCRIPT_%]`; laat die staan.
  Commits met `[skip release]` veroorzaken geen release.

## Wat je als Claude mag en niet mag

**Veilig om te wijzigen** (daarna door de gebruiker via Gitoqlok in de dev-app te pullen):
- Laadscript-tabs in `data_load_script/`. Houd `loadScript.qvs` hiermee consistent
  *(nog te verifiëren: welke van de twee Gitoqlok bij een pull als bron gebruikt — vraag dit
  na bij de eerste wijziging en leg het hier vast)*.
- Inhoud van measures, dimensions en variabelen in bestaande objecten.

**Niet doen:**
- `qId`'s wijzigen of verzinnen; een nieuw object krijgt zijn ID van Qlik, dus maak
  nieuwe sheets/objecten in de app, niet als JSON.
- `appprops.json`, media-URL's (`/api/v1/apps/<app-id>/media/...`) en app-ID's aanpassen.
- `fields.json` bewerken: dit is gegenereerd en wisselt per app.
- Bookmarks aanmaken of verwijderen.
- Script-created variabelen met een timestamp (zoals `vJobTimestamp`) als inhoudelijke
  wijziging beschouwen; die veranderen bij elke reload.
- Een *complete* sheet-JSON herschrijven of herformatteren. Eén regel wijzigen is een
  review-bare diff; een volledig herschreven bestand (±1000 regels) is dat niet.

## Conventies

- Tabnamen in het laadscript beginnen met een emoji-prefix: `🛠️` Main/variabelen,
  `🔅` dimensietabellen (`D_…`), `📊` feitentabellen (`F_…`), `📒` mappings, `✂️` filters,
  `🧹` clean up.
- Measure-patroon: `[Naam]` (basis), `[Naam CY]`, `[Naam PY]`, `[Naam PY Δ]`, `[Naam PY Δ %]`.
- Gedeelde subroutines (kalender, variabelenlijst) staan in repo `Default-Subroutines`
  en worden daar onderhouden, niet gekopieerd naar de app-repo.
- Datalagen: `MCF-Extract` → `MCF-Transform` (QVD's) → app-repo's. Pas een app niet aan
  voor iets dat in de transformlaag hoort.

## Werkwijze

1. Maak een `feature/<korte-naam>`-branch vanaf `dev`.
2. Houd wijzigingen klein en beschrijf *wat en waarom* in het commitbericht.
3. Open een PR naar `dev`; laat de changelog-preview draaien.
4. Zeg expliciet wat de gebruiker daarna in Gitoqlok moet doen (pull in dev-app, controleren,
   later pull in prd).

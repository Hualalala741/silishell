# Data directory

The repository keeps acquisition code and source metadata close to the project,
but does not version generated database exports, map caches, browser output, or
large public rasters that can be restored from their publishers.

## Local-only source workbook

- `raw/2000-2024数据1.xlsx` is expected by the ODS import script but is
  deliberately ignored by Git. Its upstream publisher, original URL,
  methodology, and redistribution licence are unresolved. Obtain an authorised
  copy separately and do not publish it as an official open dataset until that
  provenance is supplied.

## Reproducible public inputs

Run these from the repository root:

```bash
uv run python scripts/fetch_public_data.py boundaries
uv run python scripts/fetch_public_data.py admin-codes
uv run python scripts/fetch_public_data.py lulc
uv run python scripts/fetch_public_data.py worldpop
uv run python scripts/fetch_public_data.py osm
```

Or run every step with `uv run python scripts/fetch_public_data.py all`.
Existing files are preserved unless `--force` is supplied.

The script records and uses these upstream sources:

- geoBoundaries CHN ADM2 (PDDL 1.0)
- yescallop/areacodes at the pinned commit (CC0 1.0)
- Microsoft Planetary Computer `io-lulc-annual-v02` (CC BY 4.0)
- WorldPop China 100m Population 2020 (CC BY 4.0)
- OpenStreetMap contributors through Overpass (ODbL 1.0)

The OSM command builds the five verified core layers: major roads, motorway
exits, rivers, settlements, and town centres. Market, logistics, and
agricultural POI layers are optional inputs to the metric script and are not
downloaded by the current acquisition command.

Generated files remain ignored by Git. Preserve attribution when publishing
derived results, and review each upstream licence before redistribution.

`processed/county_context.json` is also versioned because it is a small,
human-reviewable input. Rebuild it from NASA POWER and the cited government
rates with:

```bash
uv run python scripts/fetch_climate_risk.py
uv run python scripts/build_county_context.py
```

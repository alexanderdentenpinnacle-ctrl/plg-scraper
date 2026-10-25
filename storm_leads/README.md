# Storm Impact → Commercial Property List

This tool maps recent US hail and hurricane damage zones, matches them against commercial and industrial parcels, and produces a lead list ready to import into GHL, plus an interactive map.

## Rerun

```bash
cd storm_leads
pip install -r requirements.txt
python run.py --days 90 --min-hail 1.25
```

| Flag | What it does |
|---|---|
| `--days N` / `--min-hail X` | Override `window_days` / `min_hail_in` from `config.yaml` (the MESH threshold follows the inch value) |
| `--end YYYY-MM-DD` | End the window on a date other than today |
| `--offline` | Rebuild from the `data/raw` cache without touching the network |
| `--check-sources` | Ping every parcel source and confirm its field names. **Run this once before the first real run.** |
| `--mark-pushed` | After you import `output/ghl_import.csv` into GHL, mark those rows as pushed |

Change scope settings (window, hail size, storms, counties, land-use codes) in `config.yaml`, not in the code.

## Outputs (`output/`)

| File | Contents |
|---|---|
| `affected_commercial.csv` | One row per parcel. Cumulative: each run appends new parcels and updates rows it sees again. Includes every spec field plus `owner_is_entity`, `all_events`, `match_method`, `first_seen`, `is_new_this_run`, `ghl_pushed`, `needs_ghl_push`. |
| `ghl_import.csv` | Only the rows that still need to go to GHL, with GHL headers (owner as contact, property as custom fields, tags) |
| `ghl_column_mapping.csv` | GHL header → source field. Create the custom fields in GHL once, or map them in the import wizard. |
| `new_this_run.csv` | Parcels first seen in this run |
| `affected_events.csv` | One row per parcel per event (a parcel can be hit by hail and a hurricane) |
| `map.html` | Leaflet map: hail swaths colored by size (dashed = radar-only), 50/64-kt wind zones with the track line, SPC reports, and clustered parcel markers with popups |
| `run_report.txt` | Row counts per county and per event (also printed at the end of each run) |

The primary event on each parcel row is the hurricane if one hit it (64 kt ranks above 50 kt). Otherwise it's the largest hail. `all_events` lists every event that hit the parcel.

## Pipeline (`pipeline/`)

1. `fetch_storms.py` downloads the SPC filtered hail CSVs (per day), the MRMS `MESH_Max_1440min` file valid 12Z the next morning (it matches SPC's 12Z–12Z convective day), the ATCF best-track b-decks for every storm in the window, the NHC best-track GIS zip, and Census state outlines.
2. `build_footprints.py`
   - **Hail:** keeps MESH ≥ 32 mm, splits it into connected regions per day, and records the max size per region. A region with no SPC report within 10 mi on the same day gets `radar_only_flag`. On a day with no MRMS file, it buffers that day's SPC reports by 1 mi instead (`source=spc_points`).
   - **Hurricanes:** uses the NHC best-track `windswath` (RADII 50/64) when it's posted. Otherwise it builds the swath from the best-track wind radii: a quadrant polygon at each fix, consecutive fixes joined by convex hull, then unioned. Isaias is always included. Any other storm in the window is included if its 50-kt swath touches US land.
3. `fetch_parcels.py` only queries counties whose boundary touches a footprint, and only the footprint's bounding box inside them. It keeps commercial and industrial parcels only.
   - **FL:** DOR use codes 011–039 (commercial) and 041–049 (industrial). 000–009 are excluded. 010 and 040 (vacant commercial/industrial) are also excluded, to follow "no vacant land"; set `exclude_vacant: false` to keep them.
   - **AL:** a keyword classifier on the county land-use code and description.
4. `intersect.py` joins parcel centroids first (`match_method=centroid`). Parcels whose centroid misses are then checked with a polygon intersection (`polygon_edge`) so large lots clipped by a swath edge aren't dropped. A hurricane parcel's `event_date` is the date of the nearest best-track fix.
5. `export.py` / `map.py` write the CSVs, the ledger, the map and the report.

Every download is cached in `data/raw/`. Past-day 404s are cached as `.missing`, so reruns are cheap. Delete a file to force a re-download.

## Parcel sources (`config.yaml → parcels`)

* **FL (Escambia, Okaloosa, Santa Rosa, Walton):** the Florida Statewide Cadastral ArcGIS layer (DOR NAL attributes on parcel polygons), filtered by `CO_NO` and `DOR_UC` on the server. To use the DOR NAL CSV and parcel shapefile instead, drop them in `data/raw/parcels/<county>/` and switch that county to `type: file` (example in the config).
* **AL (Baldwin, Mobile):** the URLs are still `TODO`. Paste each county's ArcGIS parcel layer URL into the config, fix the `fields` names, then run `--check-sources`. These counties are skipped with a warning until a URL is set.

## Weekly automation

`.github/workflows/storm-leads.yml` runs every Monday (it can also be started by hand). Each run:

1. Restores the previous ledger and runs the pipeline.
2. Appends new events and parcels and flags the ones not yet pushed to GHL (`needs_ghl_push`).
3. Uploads `state/` and `output/` as an **encrypted** artifact.

The repo is public, so lead data is never committed. One-time setup: add the repo secret `STORM_LEADS_PASSPHRASE`.

To read a run's results, download the `storm-leads-state` artifact and run `gpg -d storm-leads.tar.gz.gpg | tar xz`. After importing into GHL, start the workflow by hand with `mark_pushed = true`.

## Map deploy (Cloudflare Pages)

`scripts/deploy_map.sh` builds `deploy/site/`. It's a dry run unless `CONFIRM=yes`. It refuses to publish owner names unless `map.include_owner_in_popup: false` or `ALLOW_OWNER_DATA=1`, because a Pages URL is public.

## Phase 2: national hail via Regrid

Not built yet. It needs a Regrid API key. It will add a `regrid` parcel source filtered on `usecode` / `lbcs_activity` for the national hail swaths.

## Tests

`python -m pytest -q` runs offline tests on synthetic fixtures: SPC parsing, DOR classification, swath building, radar-only flag, hurricane zones, the two-stage join, the ledger, and the GHL export.

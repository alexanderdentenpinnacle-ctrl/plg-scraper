"""Step 1: download raw storm data into data/raw (cached, idempotent).

- SPC filtered hail reports (one CSV per convective day)
- MRMS MESH_Max_1440min GRIB2 (one file per convective day, valid 12Z the next morning)
- NHC/ATCF best-track b-decks for every storm in the window (+ the NHC best-track GIS zip when posted)
- Census state boundaries (used to decide whether a storm made a US landfall)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .common import NotFound, Window, fetch, gunzip, log

SPC_URL = "https://www.spc.noaa.gov/climo/reports/{yymmdd}_rpts_filtered_hail.csv"
MRMS_DIR = "https://mtarchive.geol.iastate.edu/{d:%Y/%m/%d}/mrms/ncep/MESH_Max_1440min/"
BTK_CURRENT = "https://ftp.nhc.noaa.gov/atcf/btk/"
BTK_ARCHIVE = "https://ftp.nhc.noaa.gov/atcf/archive/{year}/"
NHC_BEST_TRACK_ZIP = "https://www.nhc.noaa.gov/gis/best_track/{storm_id}_best_track.zip"
STATES_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_20m.zip"


def _recent(d: date, days: int = 3) -> bool:
    return (date.today() - d).days < days


# ---------------------------------------------------------------- SPC hail
def fetch_spc_hail(window: Window, offline=False) -> pd.DataFrame:
    """All SPC filtered hail reports in the window. Size converted to inches."""
    rows, missing = [], 0
    for d in window.days():
        url = SPC_URL.format(yymmdd=d.strftime("%y%m%d"))
        try:
            p = fetch(url, "spc", refresh=_recent(d) and not offline,
                      negative_cache=not _recent(d), offline=offline)
        except NotFound:
            missing += 1
            continue
        rows.extend(parse_spc_csv(p.read_text(errors="replace"), d))
    if missing:
        log.info("SPC: %d day(s) had no hail file (no reports or not posted)", missing)
    df = pd.DataFrame(rows, columns=["event_date", "time_utc", "size_in", "location", "county",
                                     "state", "lat", "lon", "comments"])
    log.info("SPC: %d hail reports in window", len(df))
    return df


def parse_spc_csv(text: str, d: date) -> list[list]:
    """Parse an SPC *_rpts_filtered_hail.csv. Comments may contain commas, so split at most 7 times."""
    out = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("Time"):
            continue
        parts = line.split(",", 7)
        if len(parts) < 7:
            continue
        try:
            size = int(parts[1]) / 100.0  # hundredths of an inch
            lat, lon = float(parts[5]), float(parts[6])
        except ValueError:
            continue
        out.append([d, parts[0], size, parts[2], parts[3], parts[4], lat, lon,
                    parts[7].strip() if len(parts) > 7 else ""])
    return out


# ---------------------------------------------------------------- MRMS MESH
_MRMS_RE = re.compile(r"MESH_Max_1440min_00\.50_(\d{8})-(\d{6})\.grib2\.gz")


def fetch_mrms_day(d: date, offline=False):
    """GRIB2 path for convective day d (24-h max ending 12Z on d+1), or None if unavailable."""
    nxt = d + timedelta(days=1)
    try:
        idx = fetch(MRMS_DIR.format(d=nxt), "mrms/index", refresh=_recent(nxt) and not offline,
                    negative_cache=not _recent(nxt), offline=offline)
    except (NotFound, Exception) as e:  # noqa: BLE001 - one bad day shouldn't stop the run
        log.debug("MRMS index %s unavailable: %s", nxt, e)
        return None
    names = sorted(set(_MRMS_RE.findall(idx.read_text(errors="replace"))))
    if not names:
        return None
    # file closest to 12:00:00Z
    ymd, hms = min(names, key=lambda n: abs(int(n[1]) - 120000))
    url = MRMS_DIR.format(d=nxt) + f"MESH_Max_1440min_00.50_{ymd}-{hms}.grib2.gz"
    try:
        return gunzip(fetch(url, f"mrms/{nxt:%Y%m%d}", offline=offline))
    except NotFound:
        return None


def fetch_mrms(window: Window, offline=False) -> dict[date, object]:
    out = {}
    for d in window.days():
        if d >= date.today():  # 24-h window not complete yet
            continue
        p = fetch_mrms_day(d, offline=offline)
        if p is not None:
            out[d] = p
    log.info("MRMS: %d/%d day files available", len(out), sum(1 for _ in window.days()))
    return out


# ---------------------------------------------------------------- Hurricanes
_BTK_RE = re.compile(r"b(al|ep|cp)(\d\d)(\d{4})\.dat(?:\.gz)?")


def list_bdecks(year: int, basins, offline=False) -> dict[str, str]:
    """storm_id (e.g. al092026) -> b-deck URL. Invests (90-99) are skipped."""
    found = {}
    for base in (BTK_ARCHIVE.format(year=year), BTK_CURRENT):
        try:
            idx = fetch(base, f"atcf/index/{year}", refresh=not offline,
                        negative_cache=False, offline=offline)
        except Exception as e:  # noqa: BLE001
            log.debug("ATCF index %s unavailable: %s", base, e)
            continue
        for m in _BTK_RE.finditer(idx.read_text(errors="replace")):
            basin, num, yr = m.groups()
            if int(yr) != year or basin not in basins or int(num) >= 90:
                continue
            found[f"{basin}{num}{yr}"] = base + m.group(0)
    return found


def fetch_bdecks(window: Window, basins, years=None, offline=False) -> dict[str, object]:
    years = years or sorted({window.start.year, window.end.year})
    paths = {}
    for y in years:
        for sid, url in list_bdecks(y, basins, offline=offline).items():
            try:
                paths[sid] = gunzip(fetch(url, f"atcf/{y}", refresh=not offline,
                                          negative_cache=False, offline=offline))
            except Exception as e:  # noqa: BLE001
                log.warning("b-deck %s failed: %s", sid, e)
    log.info("ATCF: %d storm b-decks", len(paths))
    return paths


def fetch_nhc_best_track_zip(storm_id: str, offline=False):
    try:
        return fetch(NHC_BEST_TRACK_ZIP.format(storm_id=storm_id), "nhc",
                     negative_cache=False, offline=offline)
    except Exception as e:  # noqa: BLE001
        log.info("NHC best-track GIS zip not available for %s (%s); will build swath from wind radii",
                 storm_id, type(e).__name__)
        return None


def fetch_us_states(offline=False):
    try:
        return fetch(STATES_URL, "census", offline=offline)
    except Exception as e:  # noqa: BLE001
        log.warning("Census states download failed (%s); landfall check will use the parcel counties only", e)
        return None


def run(window: Window, cfg: dict, offline=False) -> dict:
    hcfg = cfg["hurricane"]
    years = [hcfg["year"]] if hcfg.get("year") else None
    return {
        "spc": fetch_spc_hail(window, offline=offline),
        "mrms": fetch_mrms(window, offline=offline),
        "bdecks": fetch_bdecks(window, hcfg["basins"], years, offline=offline),
        "states": fetch_us_states(offline=offline),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

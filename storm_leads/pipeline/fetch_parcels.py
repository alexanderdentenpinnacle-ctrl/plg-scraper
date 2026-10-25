"""Step 3: fetch commercial/industrial parcels that could fall inside a storm footprint.

Only counties whose boundary touches a footprint are queried, and only the footprint's
bounding box inside that county, so the downloads stay small. Responses are cached.
"""
from __future__ import annotations

import glob
import hashlib
import json
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from .common import CRS_LL, RAW, _session, fetch, log
from .land_use import FL_DOR, classify_fl_dor, classify_keyword, norm_dor

COUNTIES_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip"
STATE_FIPS = {"FL": "12", "AL": "01"}
STD_FIELDS = ["parcel_id", "land_use_code", "land_use_desc", "situs_address", "city", "zip",
              "building_sqft", "year_built", "market_value", "owner_name", "owner_addr1",
              "owner_addr2", "owner_city", "owner_state", "owner_zip"]


def resolve_source(county: dict, cfg: dict) -> dict:
    src = county["source"]
    return dict(cfg["parcels"][src]) if isinstance(src, str) else dict(src)


def county_shapes(counties: list[dict], offline=False) -> dict[str, object]:
    """county name -> geometry (Census 1:20m). Missing file -> {} (every county gets queried)."""
    try:
        p = fetch(COUNTIES_URL, "census", offline=offline)
        g = gpd.read_file(f"zip://{p}").to_crs(CRS_LL)
    except Exception as e:  # noqa: BLE001
        log.warning("Census counties unavailable (%s); querying every configured county", e)
        return {}
    out = {}
    for c in counties:
        m = g[(g.STATEFP == STATE_FIPS.get(c["state"], "")) & (g.NAME.str.lower() == c["name"].lower())]
        if len(m):
            out[c["name"]] = m.geometry.iloc[0]
    return out


# ---------------------------------------------------------------- ArcGIS REST
def _arcgis_post(url: str, params: dict, cache_dir: Path, offline=False) -> dict:
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
    cp = cache_dir / f"{key}.json"
    if cp.exists():
        return json.loads(cp.read_text())
    if offline:
        raise FileNotFoundError(f"offline and not cached: {url}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    delay = 2
    for attempt in range(5):
        try:
            r = _session.post(url, data=params, timeout=120)
            r.raise_for_status()
            js = r.json()
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                raise
            log.warning("ArcGIS %s failed (%s); retry in %ss", url, e, delay)
            time.sleep(delay)
            delay *= 2
    if "error" not in js:
        cp.write_text(json.dumps(js))
    return js


def fetch_arcgis(src: dict, bbox, where_options: list[str], cache_dir: Path, offline=False) -> gpd.GeoDataFrame:
    url = src["url"].rstrip("/") + "/query"
    geom = {"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
            "spatialReference": {"wkid": 4326}}
    base = {"geometry": json.dumps(geom), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "f": "json"}
    ids, used_where = None, None
    for where in where_options:  # first clause the server accepts wins
        js = _arcgis_post(url, {**base, "where": where, "returnIdsOnly": "true"}, cache_dir, offline)
        if "error" not in js:
            ids, used_where = js.get("objectIds") or [], where
            break
        log.debug("where %r rejected: %s", where, js["error"])
    if ids is None:
        raise RuntimeError(f"ArcGIS layer rejected every query: {src['url']}")
    log.info("  %d candidate parcels (where: %s)", len(ids), used_where)
    feats = []
    ids = sorted(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        js = _arcgis_post(url, {"objectIds": ",".join(map(str, chunk)), "outFields": "*",
                                "outSR": 4326, "f": "geojson", "returnGeometry": "true"}, cache_dir, offline)
        feats.extend(js.get("features", []))
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs=CRS_LL)
    return gpd.GeoDataFrame.from_features(feats, crs=CRS_LL)


def _fl_where(src: dict, county: dict, cfg: dict) -> list[str]:
    f = src["fields"]
    co, uc = f.get("county_no"), f["land_use_code"]
    lo, hi = cfg["land_use"]["fl_commercial"][0], cfg["land_use"]["fl_industrial"][1]
    cw = f"{co} = {int(county['dor_county_no'])}" if co and county.get("dor_county_no") else "1=1"
    return [f"{cw} AND {uc} >= '{lo}' AND {uc} <= '{hi}'",   # text use codes
            f"{cw} AND {uc} >= {int(lo)} AND {uc} <= {int(hi)}",  # numeric use codes
            cw]                                               # filter client-side


# ---------------------------------------------------------------- local files
def fetch_file(src: dict, clip_geom) -> gpd.GeoDataFrame:
    paths = sorted(glob.glob(str(Path(RAW.parent.parent, src["geometry"]))) or glob.glob(src["geometry"]))
    if not paths:
        raise FileNotFoundError(f"no parcel geometry matches {src['geometry']}")
    p = paths[0]
    g = gpd.read_file(f"zip://{p}" if p.endswith(".zip") else p,
                      bbox=tuple(clip_geom.bounds) if clip_geom is not None else None).to_crs(CRS_LL)
    if src.get("nal_csv"):
        nal_paths = sorted(glob.glob(str(Path(RAW.parent.parent, src["nal_csv"]))) or glob.glob(src["nal_csv"]))
        if not nal_paths:
            raise FileNotFoundError(f"no NAL CSV matches {src['nal_csv']}")
        want = {v for k, v in src["fields"].items() if v} | {src["join_key"]}
        nal = pd.read_csv(nal_paths[0], dtype=str, usecols=lambda c: c in want, low_memory=False)
        key = src["join_key"]
        gkey = src.get("geometry_join_key", key)
        g = g[[gkey, "geometry"]].merge(nal, left_on=gkey, right_on=key, how="inner")
    return g


# ---------------------------------------------------------------- normalize
def normalize(raw: gpd.GeoDataFrame, src: dict, county: dict, cfg: dict) -> gpd.GeoDataFrame:
    f = src["fields"]
    cols = {}
    for k in STD_FIELDS:
        col = f.get(k)
        cols[k] = raw[col] if col and col in raw.columns else pd.Series(None, index=raw.index, dtype=object)
    df = gpd.GeoDataFrame(pd.DataFrame(cols), geometry=raw.geometry.values, crs=raw.crs)
    if src.get("use_code_type") == "fl_dor":
        if f.get("county_no") and f["county_no"] in raw.columns and county.get("dor_county_no"):
            keep = pd.to_numeric(raw[f["county_no"]], errors="coerce") == int(county["dor_county_no"])
            df = df[keep.values]
        df["land_use_code"] = df["land_use_code"].map(norm_dor)
        df["land_use_class"] = classify_fl_dor(df["land_use_code"], cfg)
        df["land_use_desc"] = df["land_use_code"].map(FL_DOR).fillna(df["land_use_desc"])
    else:
        df["land_use_class"] = classify_keyword(df["land_use_code"], df["land_use_desc"], cfg)
    df = df[df["land_use_class"].notna() & df.geometry.notna() & ~df.geometry.is_empty].copy()
    df["county"], df["state"] = county["name"], county["state"]
    for c in ("building_sqft", "year_built", "market_value"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[df.year_built <= 0, "year_built"] = pd.NA
    df["parcel_id"] = df["parcel_id"].astype(str).str.strip()
    df = df.drop_duplicates(["parcel_id"])
    return df.reset_index(drop=True)


def run(footprints: dict, cfg: dict, offline=False) -> gpd.GeoDataFrame:
    fp = [g for g in (footprints["hail"], footprints["hurricane"]) if len(g)]
    if not fp:
        log.info("Parcels: no footprints, nothing to fetch")
        return gpd.GeoDataFrame(columns=STD_FIELDS + ["county", "state", "geometry"], geometry="geometry", crs=CRS_LL)
    all_fp = unary_union([unary_union(g.geometry) for g in fp])
    counties = cfg["parcels"]["counties"]
    shapes = county_shapes(counties, offline=offline)
    frames = []
    for c in counties:
        src = resolve_source(c, cfg)
        cg = shapes.get(c["name"])
        area = all_fp.intersection(cg) if cg is not None else all_fp
        if area.is_empty:
            log.info("Parcels: %s, %s - no storm footprint, skipped", c["name"], c["state"])
            continue
        if src.get("type") == "arcgis" and not src.get("url"):
            log.warning("Parcels: %s, %s - no source URL configured (config.yaml), skipped", c["name"], c["state"])
            continue
        log.info("Parcels: %s, %s", c["name"], c["state"])
        cache = RAW / "parcels" / f"{c['state']}_{c['name'].replace(' ', '_').lower()}"
        try:
            if src["type"] == "arcgis":
                where = _fl_where(src, c, cfg) if src.get("use_code_type") == "fl_dor" else ["1=1"]
                raw = fetch_arcgis(src, area.bounds, where, cache, offline=offline)
            elif src["type"] == "file":
                raw = fetch_file(src, area)
            else:
                raise ValueError(f"unknown source type {src['type']}")
        except Exception as e:  # noqa: BLE001 - one county failing shouldn't sink the run
            log.error("Parcels: %s, %s failed: %s", c["name"], c["state"], e)
            continue
        if raw.empty:
            continue
        norm = normalize(raw, src, c, cfg)
        # keep only parcels whose polygon touches this county's footprint
        norm = norm[norm.intersects(area)]
        log.info("  %d commercial/industrial parcels in footprint", len(norm))
        frames.append(norm)
    if not frames:
        return gpd.GeoDataFrame(columns=STD_FIELDS + ["county", "state", "land_use_class", "geometry"],
                                geometry="geometry", crs=CRS_LL)
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=CRS_LL)


def check_sources(cfg: dict) -> int:
    """Ping each configured parcel source and print its fields. Returns # of failures."""
    bad = 0
    seen = set()
    for c in cfg["parcels"]["counties"]:
        src = resolve_source(c, cfg)
        label = f"{c['name']}, {c['state']}"
        if src.get("type") != "arcgis":
            ok = bool(glob.glob(src.get("geometry", "")))
            print(f"[{'OK' if ok else 'MISSING'}] {label}: file {src.get('geometry')}")
            bad += not ok
            continue
        if not src.get("url"):
            print(f"[TODO] {label}: no URL configured")
            bad += 1
            continue
        if src["url"] in seen:
            print(f"[OK] {label}: shares {src['url']}")
            continue
        seen.add(src["url"])
        try:
            js = _session.get(src["url"], params={"f": "json"}, timeout=60).json()
            names = {f["name"] for f in js.get("fields", [])}
            missing = [v for v in src["fields"].values() if v and v not in names]
            print(f"[{'OK' if not missing else 'FIELDS?'}] {label}: {js.get('name')} "
                  f"max={js.get('maxRecordCount')} missing_fields={missing}")
            bad += bool(missing)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {label}: {e}")
            bad += 1
    return bad


"""Step 2: turn raw storm data into damage footprints.

Hail:      MRMS MESH >= threshold -> connected regions per day, each with its max size.
           Regions with no SPC report within N miles get radar_only_flag = True.
Hurricane: 50-kt and 64-kt wind swaths, from the NHC best-track GIS zip when it is posted,
           otherwise built from the ATCF best-track wind radii (quadrant sectors at each fix,
           consecutive fixes joined by convex hull, then unioned).
"""
from __future__ import annotations

import zipfile
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union

from .common import CRS_EQ, CRS_LL, MILE_M, NM_M, WORK, Window, log

GEOD = Geod(ellps="WGS84")
MM_PER_IN = 25.4


# ================================================================ hail
def read_mesh(path: Path):
    """Return (array_mm, affine_transform) with lon in -180..180. MRMS missing values are < 0."""
    import rasterio
    from rasterio.transform import Affine

    try:
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float32")
            tr = src.transform
            nodata = src.nodata
        if nodata is not None:
            arr[arr == nodata] = -1
    except Exception:  # GDAL without GRIB support -> cfgrib
        import xarray as xr

        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
        var = list(ds.data_vars)[0]
        arr = ds[var].values.astype("float32")
        lats, lons = ds.latitude.values, ds.longitude.values
        dx, dy = float(lons[1] - lons[0]), float(lats[1] - lats[0])
        if dy > 0:  # make north-up
            arr, lats, dy = arr[::-1], lats[::-1], -dy
        tr = Affine(dx, 0, float(lons[0]) - dx / 2, 0, dy, float(lats[0]) - dy / 2)
    arr = np.nan_to_num(arr, nan=-1)
    if tr.c > 180:  # MRMS stores 230..300 E
        tr = Affine(tr.a, tr.b, tr.c - 360, tr.d, tr.e, tr.f)
    return arr, tr


def polygonize_mesh(arr, tr, threshold_mm: float, simplify_deg: float = 0.0) -> gpd.GeoDataFrame:
    """Connected regions where MESH >= threshold, each with its max MESH (mm)."""
    from rasterio.features import shapes
    from rasterio.transform import Affine
    from scipy import ndimage

    mask = arr >= threshold_mm
    if not mask.any():
        return gpd.GeoDataFrame(columns=["max_mesh_mm", "geometry"], geometry="geometry", crs=CRS_LL)
    # crop to the hit area so polygonizing is cheap
    rows, cols = np.where(mask)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    sub, submask = arr[r0:r1, c0:c1], mask[r0:r1, c0:c1]
    subtr = tr @ Affine.translation(c0, r0) if hasattr(tr, "__matmul__") else tr * Affine.translation(c0, r0)
    labels, n = ndimage.label(submask, structure=np.ones((3, 3)))
    maxes = ndimage.maximum(sub, labels, index=np.arange(1, n + 1))
    geoms = {}
    for geom, val in shapes(labels.astype("int32"), mask=submask, transform=subtr, connectivity=8):
        geoms.setdefault(int(val), []).append(shape(geom))
    recs = []
    for lab, parts in geoms.items():
        g = unary_union(parts)
        if simplify_deg:
            g = g.simplify(simplify_deg, preserve_topology=True).buffer(0)
        recs.append({"max_mesh_mm": float(maxes[lab - 1]), "geometry": g})
    return gpd.GeoDataFrame(recs, geometry="geometry", crs=CRS_LL)


def spc_points(spc: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(spc.copy(), geometry=gpd.points_from_xy(spc.lon, spc.lat), crs=CRS_LL)


def flag_radar_only(swaths: gpd.GeoDataFrame, pts: gpd.GeoDataFrame, radius_mi: float) -> pd.Series:
    """True where no SPC report on the same convective day lies within radius_mi of the swath."""
    if swaths.empty:
        return pd.Series(dtype=bool)
    flags = pd.Series(True, index=swaths.index)
    if pts.empty:
        return flags
    sw = swaths.to_crs(CRS_EQ)
    pp = pts.to_crs(CRS_EQ)
    for d, grp in sw.groupby("event_date"):
        day_pts = pp[pp.event_date == d]
        if day_pts.empty:
            continue
        near = gpd.sjoin_nearest(grp[["geometry"]], day_pts[["geometry"]],
                                 max_distance=radius_mi * MILE_M, how="left")
        hit = near.index[near["index_right"].notna()].unique()
        flags.loc[hit] = False
    return flags


def build_hail(mrms: dict, spc: pd.DataFrame, window: Window, cfg: dict) -> gpd.GeoDataFrame:
    thr = cfg["mrms_mesh_threshold_mm"]
    pts = spc_points(spc)
    frames = []
    for d, path in sorted(mrms.items()):
        try:
            arr, tr = read_mesh(path)
        except Exception as e:  # noqa: BLE001
            log.warning("MRMS %s unreadable (%s); falling back to SPC points for that day", d, e)
            continue
        g = polygonize_mesh(arr, tr, thr, cfg.get("swath_simplify_deg", 0))
        if g.empty:
            continue
        g["event_date"] = d
        g["source"] = "mrms"
        frames.append(g)

    # Days with no MRMS file: buffer the SPC reports that meet the size threshold
    buf_mi = cfg.get("spc_point_fallback_buffer_mi") or 0
    have = set(mrms)
    if buf_mi and not pts.empty:
        fb = pts[(~pts.event_date.isin(have)) & (pts.size_in >= cfg["min_hail_in"])]
        if not fb.empty:
            fbe = fb.to_crs(CRS_EQ)
            fbe["geometry"] = fbe.buffer(buf_mi * MILE_M)
            dis = fbe.dissolve(by="event_date", aggfunc={"size_in": "max"}).explode(index_parts=False)
            dis = dis.reset_index().to_crs(CRS_LL)
            dis["max_mesh_mm"] = dis["size_in"] * MM_PER_IN
            dis["source"] = "spc_points"
            frames.append(dis[["event_date", "max_mesh_mm", "source", "geometry"]])
            log.info("Hail: %d day(s) used SPC-point fallback (no MRMS file)", fb.event_date.nunique())

    if not frames:
        return gpd.GeoDataFrame(columns=["event_type", "event_id", "event_date", "hail_size_in",
                                         "max_mesh_mm", "radar_only_flag", "source", "geometry"],
                                geometry="geometry", crs=CRS_LL)
    hail = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=CRS_LL)
    hail["hail_size_in"] = (hail["max_mesh_mm"] / MM_PER_IN).round(2)
    hail = hail[hail["hail_size_in"] >= cfg["min_hail_in"] - 1e-9].reset_index(drop=True)
    hail["radar_only_flag"] = False
    is_radar = hail.source == "mrms"
    hail.loc[is_radar, "radar_only_flag"] = flag_radar_only(
        hail[is_radar], pts, cfg["radar_only_radius_mi"]).values
    hail["event_type"] = "hail"
    hail["event_id"] = ["hail_" + pd.Timestamp(d).strftime("%Y%m%d") for d in hail.event_date]
    log.info("Hail: %d swath polygons over %d days (%d radar-only)",
             len(hail), hail.event_date.nunique(), int(hail.radar_only_flag.sum()))
    return hail[["event_type", "event_id", "event_date", "hail_size_in", "max_mesh_mm",
                 "radar_only_flag", "source", "geometry"]]


# ================================================================ hurricanes
BDECK_COLS = ["basin", "cy", "dtg", "technum", "tech", "tau", "lat", "lon", "vmax", "mslp", "ty",
              "rad", "windcode", "rad1", "rad2", "rad3", "rad4"]


def _ll(v: str) -> float:
    v = v.strip()
    x = int(v[:-1]) / 10.0
    return -x if v[-1] in "SW" else x


def parse_bdeck(path: Path) -> pd.DataFrame:
    recs = []
    for line in Path(path).read_text(errors="replace").splitlines():
        p = [s.strip() for s in line.split(",")]
        if len(p) < 17 or p[4] != "BEST":
            continue
        try:
            r = dict(zip(BDECK_COLS, p[:17]))
            r["dtg"] = datetime.strptime(r["dtg"], "%Y%m%d%H")
            r["lat"], r["lon"] = _ll(r["lat"]), _ll(r["lon"])
            for k in ("vmax", "rad", "rad1", "rad2", "rad3", "rad4"):
                r[k] = int(r[k] or 0)
            r["name"] = p[27] if len(p) > 27 else ""
        except (ValueError, IndexError):
            continue
        recs.append(r)
    df = pd.DataFrame(recs)
    if not df.empty:
        df["storm_id"] = (df.basin.str.lower() + df.cy.str.zfill(2) + df.dtg.dt.year.astype(str).iloc[0])
        # the name evolves (INVEST -> NINE -> ISAIAS); use the last one
        df["name"] = df["name"].replace("", np.nan).ffill().bfill().iloc[-1] if df["name"].any() else ""
    return df


def _quadrant_polygon(lat, lon, radii_nm: tuple, steps: int = 12) -> Polygon | None:
    """NE, SE, SW, NW quadrant radii (nm) -> geodesic polygon (lon/lat)."""
    if not any(radii_nm):
        return None
    pts = []
    for q, r_nm in enumerate(radii_nm):  # quadrant q spans bearings q*90 .. (q+1)*90
        az = np.linspace(q * 90, (q + 1) * 90, steps)
        if r_nm <= 0:
            pts.append((lon, lat))
            continue
        lons, lats, _ = GEOD.fwd(np.full(steps, lon), np.full(steps, lat), az, np.full(steps, r_nm * NM_M))
        pts.extend(zip(lons, lats))
    poly = Polygon(pts)
    return poly if poly.is_valid else poly.buffer(0)


def swath_from_bdeck(bt: pd.DataFrame, kt: int):
    """Union of wind-radii polygons at each fix, joined fix-to-fix by convex hull."""
    rows = bt[bt.rad == kt].sort_values("dtg").drop_duplicates("dtg")
    polys = []
    for _, r in rows.iterrows():
        polys.append(_quadrant_polygon(r.lat, r.lon, (r.rad1, r.rad2, r.rad3, r.rad4)))
    pieces = []
    for a, b in zip(polys, polys[1:] + [None]):
        if a is None:
            continue
        pieces.append(a.union(b).convex_hull if b is not None else a)
    return unary_union(pieces) if pieces else None


def swath_from_nhc_zip(zpath: Path, kt: int):
    with zipfile.ZipFile(zpath) as z:
        shp = [n for n in z.namelist() if "windswath" in n.lower() and n.lower().endswith(".shp")]
    if not shp:
        return None
    g = gpd.read_file(f"zip://{zpath}!{shp[0]}").to_crs(CRS_LL)
    col = next((c for c in g.columns if c.upper() == "RADII"), None)
    if col is None:
        return None
    sel = g[g[col].astype(float).astype(int) == kt]
    return unary_union(sel.geometry) if len(sel) else None


def build_hurricanes(bdecks: dict, states_zip, window: Window, cfg: dict, offline=False) -> tuple:
    from .fetch_storms import fetch_nhc_best_track_zip

    hcfg = cfg["hurricane"]
    named = {n.upper() for n in hcfg.get("named_storms", [])}
    zones_kt = sorted(hcfg["wind_zones_kt"])
    us = None
    if states_zip is not None:
        try:
            us = unary_union(gpd.read_file(f"zip://{states_zip}").to_crs(CRS_LL).geometry)
        except Exception as e:  # noqa: BLE001
            log.warning("could not read states file: %s", e)

    t0, t1 = pd.Timestamp(window.start), pd.Timestamp(window.end) + pd.Timedelta(days=1)
    zone_recs, track_recs = [], []
    for sid, path in sorted(bdecks.items()):
        bt = parse_bdeck(path)
        if bt.empty or not ((bt.dtg >= t0) & (bt.dtg < t1)).any():
            continue
        name = str(bt["name"].iloc[0]).upper()
        if bt.vmax.max() < min(zones_kt):
            continue
        nhc_zip = fetch_nhc_best_track_zip(sid, offline=offline)
        swaths = {}
        for kt in zones_kt:
            g = swath_from_nhc_zip(nhc_zip, kt) if nhc_zip else None
            src = "nhc_best_track_gis"
            if g is None or g.is_empty:
                g, src = swath_from_bdeck(bt, kt), "atcf_wind_radii"
            if g is not None and not g.is_empty:
                swaths[kt] = (g, src)
        if not swaths:
            continue
        lowest = swaths[zones_kt[0]][0] if zones_kt[0] in swaths else next(iter(swaths.values()))[0]
        if name not in named:
            if not hcfg.get("include_other_us_landfalls", True):
                continue
            if us is None or not lowest.intersects(us):
                log.info("Hurricane: %s %s skipped (no %d-kt winds over US land)", sid, name, zones_kt[0])
                continue
        hits = bt[(bt.dtg >= t0) & (bt.dtg < t1)]
        for kt, (g, src) in swaths.items():
            zone_recs.append({"event_type": "hurricane", "event_id": f"hurricane_{sid}",
                              "storm_id": sid, "storm_name": name, "wind_zone_kt": kt,
                              "start_date": hits.dtg.min().date(), "end_date": hits.dtg.max().date(),
                              "source": src, "geometry": g})
        for _, r in bt.drop_duplicates("dtg").iterrows():
            track_recs.append({"storm_id": sid, "storm_name": name, "dtg": r.dtg, "vmax": r.vmax,
                               "geometry": Point(r.lon, r.lat)})
        log.info("Hurricane: %s %s zones=%s", sid, name, sorted(swaths))

    zones = gpd.GeoDataFrame(zone_recs, geometry="geometry", crs=CRS_LL) if zone_recs else \
        gpd.GeoDataFrame(columns=["event_type", "event_id", "storm_id", "storm_name", "wind_zone_kt",
                                  "start_date", "end_date", "source", "geometry"], geometry="geometry", crs=CRS_LL)
    track = gpd.GeoDataFrame(track_recs, geometry="geometry", crs=CRS_LL) if track_recs else \
        gpd.GeoDataFrame(columns=["storm_id", "storm_name", "dtg", "vmax", "geometry"], geometry="geometry", crs=CRS_LL)
    missing = named - set(zones.storm_name) if len(zones) else named
    if missing:
        log.warning("Hurricane: named storm(s) %s not found in ATCF b-decks for the window", sorted(missing))
    return zones, track


def run(raw: dict, window: Window, cfg: dict, offline=False) -> dict:
    hail = build_hail(raw["mrms"], raw["spc"], window, cfg)
    zones, track = build_hurricanes(raw["bdecks"], raw["states"], window, cfg, offline=offline)
    out = WORK / "footprints.gpkg"
    for layer, g in (("hail", hail), ("hurricane", zones), ("track", track),
                     ("spc", spc_points(raw["spc"]))):
        if len(g):
            g2 = g.copy()
            for c in g2.columns:
                if c != "geometry" and g2[c].dtype == object and len(g2) and isinstance(g2[c].iloc[0], date):
                    g2[c] = g2[c].astype(str)
            g2.to_file(out, layer=layer, driver="GPKG")
    return {"hail": hail, "hurricane": zones, "track": track, "spc": spc_points(raw["spc"])}

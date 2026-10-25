"""Offline tests on synthetic fixtures (no network). Run: pytest -q"""
from datetime import date, datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from pipeline import build_footprints as bf
from pipeline import export, fetch_parcels, intersect
from pipeline.common import Window, load_config
from pipeline.fetch_storms import parse_spc_csv
from pipeline.land_use import classify_fl_dor, owner_is_entity

D = date(2026, 10, 1)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def out_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "OUTPUT", tmp_path / "out")
    monkeypatch.setattr(export, "STATE", tmp_path / "state")
    monkeypatch.setattr(export, "LEDGER", tmp_path / "state" / "ledger.csv")
    (tmp_path / "out").mkdir()
    (tmp_path / "state").mkdir()
    return tmp_path


def test_parse_spc_csv_handles_commas_in_comments():
    txt = ("Time,Size,Location,County,State,Lat,Lon,Comments\n"
           "2105,175,3 N Milton,Santa Rosa,FL,30.67,-87.04,Golf ball hail, roof damage. (MOB)\n"
           "2110,bad,row\n")
    rows = parse_spc_csv(txt, D)
    assert len(rows) == 1
    assert rows[0][2] == 1.75 and rows[0][-1].startswith("Golf ball hail, roof")


def test_fl_dor_classification(cfg):
    s = pd.Series(["0011", "10", "039", "040", "48", "001", "000", "070", None])
    assert [x if isinstance(x, str) else None for x in classify_fl_dor(s, cfg)] == ["commercial", None, "commercial", None, "industrial",
                                                None, None, None, None]


def test_owner_is_entity():
    assert owner_is_entity(pd.Series(["GULF COAST HOLDINGS LLC", "SMITH JOHN A", "FIRST BAPTIST CHURCH OF MILTON",
                                      "DOE JANE TRUSTEE"])).tolist() == [True, False, True, True]


def _write_mesh(path):
    # 0.01 deg grid over the FL panhandle, MRMS-style 0..360 longitudes
    w, h = 300, 200
    arr = np.full((h, w), -1, dtype="float32")
    arr[50:80, 50:90] = 35.0    # ~1.38" swath near 30.5N, -87.0
    arr[60:65, 60:65] = 52.0    # 2.05" core
    arr[150:160, 250:260] = 40  # second swath, far from any report -> radar-only
    tr = from_origin(360 - 87.5, 31.0, 0.01, 0.01)
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
                       crs="EPSG:4326", transform=tr) as dst:
        dst.write(arr, 1)


def _bdeck(path):
    lines = []
    t0 = datetime(2026, 10, 9, 12)
    for i, (lat, lon, v) in enumerate([(28.5, -86.6, 90), (29.5, -86.55, 100), (30.4, -86.5, 95), (31.3, -86.4, 60)]):
        dtg = (t0 + timedelta(hours=6 * i)).strftime("%Y%m%d%H")
        for kt, r in ((34, 60), (50, 20), (64, 10)):
            if v < kt:
                continue
            lines.append(f"AL, 09, {dtg},   , BEST,   0, {int(lat*10)}N, {int(-lon*10)}W, {v:3d},  960, HU, "
                         f" {kt}, NEQ, {r:4d}, {r:4d}, {int(r*.8):4d}, {r:4d}, 1008,  200,  25,   0,   0,   L,   0,"
                         f"    ,   0,   0,     ISAIAS, M,")
    path.write_text("\n".join(lines) + "\n")


def _parcels():
    recs = [
        # inside 64-kt zone near Destin, commercial
        dict(PARCEL_ID="A1", CO_NO=56, DOR_UC="021", OWN_NAME="DESTIN EATS LLC", geometry=box(-86.52, 30.39, -86.519, 30.391)),
        # inside hail swath, industrial
        dict(PARCEL_ID="B2", CO_NO=27, DOR_UC="048", OWN_NAME="SMITH JOHN A", geometry=box(-87.0, 30.35, -86.999, 30.351)),
        # residential inside swath -> excluded
        dict(PARCEL_ID="C3", CO_NO=27, DOR_UC="001", OWN_NAME="DOE JANE", geometry=box(-86.98, 30.36, -86.979, 30.361)),
        # commercial, outside every footprint
        dict(PARCEL_ID="D4", CO_NO=27, DOR_UC="011", OWN_NAME="FAR AWAY INC", geometry=box(-85.0, 32.0, -84.999, 32.001)),
        # big parcel whose centroid is outside the hail swath but edge clips it -> polygon_edge
        dict(PARCEL_ID="E5", CO_NO=27, DOR_UC="017", OWN_NAME="EDGE OFFICE PARK LP", geometry=box(-87.06, 30.25, -86.99, 30.30)),
    ]
    g = gpd.GeoDataFrame(recs, crs="EPSG:4326")
    for c in ("PHY_ADDR1", "PHY_CITY", "PHY_ZIPCD", "OWN_ADDR1", "OWN_CITY", "OWN_STATE", "OWN_ZIPCD"):
        g[c] = "X"
    g["JV"], g["TOT_LVG_AREA"], g["ACT_YR_BLT"] = 500000, 4000, 1998
    return g


def test_end_to_end(tmp_path, cfg, out_dirs):
    mesh = tmp_path / "mesh.tif"
    _write_mesh(mesh)
    spc = pd.DataFrame([[D, "2105", 1.5, "Milton", "Santa Rosa", "FL", 30.45, -86.85, ""]],
                       columns=["event_date", "time_utc", "size_in", "location", "county", "state", "lat", "lon", "comments"])
    bd = tmp_path / "bal092026.dat"
    _bdeck(bd)
    window = Window(date(2026, 7, 27), date(2026, 10, 25))
    cfg["swath_simplify_deg"] = 0

    hail = bf.build_hail({D: mesh}, spc, window, cfg)
    assert len(hail) == 2
    assert hail.hail_size_in.max() == pytest.approx(2.05, abs=0.01)
    assert sorted(hail.radar_only_flag.tolist()) == [False, True]

    zones, track = bf.build_hurricanes({"al092026": bd}, None, window, cfg, offline=True)
    assert set(zones.wind_zone_kt) == {50, 64} and zones.storm_name.iloc[0] == "ISAIAS"
    fp = {"hail": hail, "hurricane": zones, "track": track, "spc": bf.spc_points(spc)}

    src = fetch_parcels.resolve_source({"source": "florida_statewide"}, cfg)
    raw = _parcels()
    parts = []
    for county, no in (("Okaloosa", 56), ("Escambia", 27)):
        parts.append(fetch_parcels.normalize(raw, src, {"name": county, "state": "FL", "dor_county_no": no}, cfg))
    parcels = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    assert set(parcels.parcel_id) == {"A1", "B2", "D4", "E5"}

    events, hits = intersect.run(parcels, fp)
    by = hits.set_index("parcel_id")
    assert set(by.index) == {"A1", "B2", "E5"}
    assert by.loc["A1", "event_type"] == "hurricane" and by.loc["A1", "wind_zone_kt"] == 64
    assert by.loc["A1", "event_date"] == date(2026, 10, 10)
    assert by.loc["B2", "event_type"] == "hail" and by.loc["B2", "match_method"] == "centroid"
    assert by.loc["E5", "match_method"] == "polygon_edge"

    export.run(hits, events, fp, cfg)
    out = out_dirs / "out"
    for f in ("affected_commercial.csv", "ghl_import.csv", "map.html", "run_report.txt", "new_this_run.csv"):
        assert (out / f).exists()
    csv = pd.read_csv(out / "affected_commercial.csv")
    assert len(csv) == 3 and csv.needs_ghl_push.all() and csv.is_new_this_run.all()
    assert csv.set_index("parcel_id").loc["A1", "owner_is_entity"]
    ghl = pd.read_csv(out / "ghl_import.csv")
    assert ghl.set_index("Parcel ID").loc["B2", "Last Name"] == "Smith"

    # second run: nothing new; after mark-pushed nothing needs a push
    export.mark_pushed()
    export.run(hits, events, fp, cfg)
    csv2 = pd.read_csv(out / "affected_commercial.csv")
    assert not csv2.is_new_this_run.any() and not csv2.needs_ghl_push.any() and csv2.ghl_pushed.all()
    assert len(pd.read_csv(out / "ghl_import.csv")) == 0

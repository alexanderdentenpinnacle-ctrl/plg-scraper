"""Step 4: match parcels to storm footprints.

Centroid join first (fast, high confidence); parcels whose centroid misses are then checked
with a full polygon intersection so large lots clipped by a swath edge aren't lost.
Returns one row per parcel per event, plus a one-row-per-parcel rollup.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from .common import CRS_EQ, log


def _two_stage_join(parcels: gpd.GeoDataFrame, zones: gpd.GeoDataFrame, cols: list[str]) -> pd.DataFrame:
    if parcels.empty or zones.empty:
        return pd.DataFrame(columns=["pidx", "match_method", *cols])
    z = zones[cols + ["geometry"]].reset_index(drop=True)
    cent = gpd.GeoDataFrame({"pidx": parcels.index}, geometry=parcels.geometry.representative_point().values,
                            crs=parcels.crs)
    a = gpd.sjoin(cent, z, predicate="within", how="inner")
    a["match_method"] = "centroid"
    rest = parcels.loc[~parcels.index.isin(a.pidx)]
    poly = gpd.GeoDataFrame({"pidx": rest.index}, geometry=rest.geometry.values, crs=parcels.crs)
    b = gpd.sjoin(poly, z, predicate="intersects", how="inner")
    b["match_method"] = "polygon_edge"
    return pd.concat([a, b], ignore_index=True)[["pidx", "match_method", *cols]]


def match_hail(parcels, hail) -> pd.DataFrame:
    m = _two_stage_join(parcels, hail, ["event_id", "event_date", "hail_size_in", "radar_only_flag", "source"])
    if m.empty:
        return m
    return (m.groupby(["pidx", "event_id"], as_index=False)
             .agg(event_date=("event_date", "first"), hail_size_in=("hail_size_in", "max"),
                  radar_only_flag=("radar_only_flag", "all"), match_method=("match_method", "first"),
                  hail_source=("source", "first"))
             .assign(event_type="hail"))


def match_hurricane(parcels, zones, track) -> pd.DataFrame:
    m = _two_stage_join(parcels, zones, ["event_id", "storm_id", "storm_name", "wind_zone_kt", "start_date"])
    if m.empty:
        return m
    m = (m.groupby(["pidx", "event_id"], as_index=False)
          .agg(storm_id=("storm_id", "first"), storm_name=("storm_name", "first"),
               wind_zone_kt=("wind_zone_kt", "max"), match_method=("match_method", "first"),
               start_date=("start_date", "first")))
    # event_date = date of the best-track fix closest to the parcel
    m["event_date"] = m["start_date"]
    if len(track):
        cent = parcels.geometry.representative_point().to_crs(CRS_EQ)
        tr = track.to_crs(CRS_EQ)
        for sid, grp in m.groupby("storm_id"):
            t = tr[tr.storm_id == sid]
            if t.empty:
                continue
            pts = gpd.GeoDataFrame({"row": grp.index}, geometry=cent.loc[grp.pidx].values, crs=CRS_EQ)
            near = gpd.sjoin_nearest(pts, t[["dtg", "geometry"]], how="left").drop_duplicates("row")
            m.loc[near.row.values, "event_date"] = pd.to_datetime(near.dtg).dt.date.values
    m["radar_only_flag"] = False
    return m.drop(columns=["start_date"]).assign(event_type="hurricane")


def _event_label(r) -> str:
    if r.event_type == "hurricane":
        return f"hurricane {r.storm_name} {int(r.wind_zone_kt)}kt {r.event_date}"
    return f"hail {r.event_date} {r.hail_size_in:.2f}in{' (radar-only)' if r.radar_only_flag else ''}"


def run(parcels: gpd.GeoDataFrame, fp: dict) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    if parcels.empty:
        return pd.DataFrame(), parcels.assign(event_count=0)
    ev = pd.concat([match_hail(parcels, fp["hail"]), match_hurricane(parcels, fp["hurricane"], fp["track"])],
                   ignore_index=True)
    if ev.empty:
        log.info("Intersect: 0 parcels inside any footprint")
        return ev, parcels.iloc[0:0].assign(event_count=0)
    for c in ("hail_size_in", "wind_zone_kt"):
        if c not in ev:
            ev[c] = pd.NA
    for c in ("storm_name", "storm_id", "hail_source"):
        if c not in ev:
            ev[c] = None
    ev["event_date"] = pd.to_datetime(ev["event_date"]).dt.date
    ev["label"] = ev.apply(_event_label, axis=1)
    # primary event per parcel: hurricane (highest kt) first, then the largest hail, then the newest
    ev["_rank"] = ev.event_type.eq("hurricane").astype(int) * 1000 + ev.wind_zone_kt.fillna(0).astype(float) \
        + ev.hail_size_in.fillna(0).astype(float)
    ev = ev.sort_values(["pidx", "_rank", "event_date"], ascending=[True, False, False])
    primary = ev.drop_duplicates("pidx").set_index("pidx")
    summary = ev.groupby("pidx").agg(event_count=("event_id", "nunique"),
                                     all_events=("label", lambda s: "; ".join(s)))
    out = parcels.loc[primary.index].copy()
    for c in ("event_type", "event_id", "event_date", "hail_size_in", "wind_zone_kt", "radar_only_flag",
              "storm_name", "match_method"):
        out[c] = primary[c]
    out = out.join(summary)
    ev = ev.drop(columns=["_rank"])
    log.info("Intersect: %d parcels, %d parcel-event matches", len(out), len(ev))
    return ev, out

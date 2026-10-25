"""Leaflet map: hail swaths by size, hurricane wind zones, clustered parcel markers."""
from __future__ import annotations

import html

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster

HAIL_BINS = [(1.25, "#ffd34d", '1.25–1.49"'), (1.5, "#ffa64d", '1.50–1.99"'), (2.0, "#ff6a33", '2.00–2.49"'),
             (2.5, "#e0263a", '2.50–2.99"'), (3.0, "#a3127a", '3.00"+')]
WIND = {50: ("#f2b134", "50-kt winds"), 64: ("#c0392b", "64-kt (hurricane) winds")}


def hail_color(size: float) -> str:
    col = HAIL_BINS[0][1]
    for lo, c, _ in HAIL_BINS:
        if size >= lo:
            col = c
    return col


def _strs(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    g = g.copy()
    for c in g.columns:
        if c != "geometry":
            g[c] = g[c].astype(str)
    return g


def build_map(fp: dict, hits: gpd.GeoDataFrame, table: pd.DataFrame, cfg: dict, path):
    mcfg = cfg.get("map", {})
    m = folium.Map(location=[37.5, -95.0], zoom_start=4, tiles="OpenStreetMap", control_scale=True)

    hz = fp["hurricane"]
    if len(hz):
        layer = folium.FeatureGroup(name="Hurricane wind zones")
        for kt in sorted(hz.wind_zone_kt.unique()):
            sub = hz[hz.wind_zone_kt == kt]
            colr, lbl = WIND.get(int(kt), ("#888", f"{kt}-kt"))
            folium.GeoJson(_strs(sub[["storm_name", "wind_zone_kt", "start_date", "end_date", "source", "geometry"]]),
                           style_function=lambda _, c=colr: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": 0.25},
                           tooltip=folium.GeoJsonTooltip(["storm_name", "wind_zone_kt", "start_date", "end_date"]),
                           name=lbl).add_to(layer)
        layer.add_to(m)
        if len(fp["track"]):
            tr = fp["track"].sort_values("dtg")
            for sid, t in tr.groupby("storm_id"):
                folium.PolyLine([(p.y, p.x) for p in t.geometry], color="#333", weight=2, dash_array="4",
                                tooltip=f"{t.storm_name.iloc[0]} track").add_to(layer)

    hail = fp["hail"]
    if len(hail):
        layer = folium.FeatureGroup(name="Hail swaths (MRMS MESH)")
        folium.GeoJson(_strs(hail[["event_date", "hail_size_in", "radar_only_flag", "source", "geometry"]]),
                       style_function=lambda f: {"color": hail_color(float(f["properties"]["hail_size_in"])),
                                                 "weight": 0.5, "fillOpacity": 0.45,
                                                 "fillColor": hail_color(float(f["properties"]["hail_size_in"])),
                                                 "dashArray": "3" if f["properties"]["radar_only_flag"] == "True" else None},
                       tooltip=folium.GeoJsonTooltip(["event_date", "hail_size_in", "radar_only_flag", "source"],
                                                     aliases=["Date", "Max size (in)", "Radar only", "Source"])).add_to(layer)
        layer.add_to(m)
    spc = fp.get("spc")
    if spc is not None and len(spc):
        layer = folium.FeatureGroup(name="SPC hail reports", show=False)
        for r in spc.itertuples():
            folium.CircleMarker((r.lat, r.lon), radius=3, color="#1f4e79", fill=True, fill_opacity=0.8,
                                tooltip=f'{r.event_date} {r.size_in:.2f}" {r.location}, {r.state}').add_to(layer)
        layer.add_to(m)

    if len(table):
        cluster = MarkerCluster(name="Affected commercial parcels").add_to(m)
        owner = mcfg.get("include_owner_in_popup", True)
        for r in table.head(mcfg.get("max_markers", 20000)).itertuples():
            ev = f"Hurricane {r.storm_name} – {int(r.wind_zone_kt)} kt" if r.event_type == "hurricane" \
                else f'Hail {float(r.hail_size_in):.2f}"' + (" (radar-only)" if r.radar_only_flag else "")
            rows = [("Event", f"{ev} on {r.event_date}"), ("All events", r.all_events),
                    ("Address", f"{r.situs_address or ''}, {r.city or ''} {r.zip or ''}"),
                    ("County", f"{r.county}, {r.state}"), ("Parcel", r.parcel_id),
                    ("Use", f"{r.land_use_code} {r.land_use_desc or ''}"),
                    ("Bldg sqft / yr", f"{r.building_sqft if pd.notna(r.building_sqft) else '–'} / "
                                       f"{int(r.year_built) if pd.notna(r.year_built) else '–'}"),
                    ("Value", f"${r.market_value:,.0f}" if pd.notna(r.market_value) else "–")]
            if owner:
                rows += [("Owner", r.owner_name), ("Mailing", r.owner_mailing_address)]
            body = "".join(f"<tr><th style='text-align:left;padding-right:6px'>{html.escape(k)}</th>"
                           f"<td>{html.escape(str(v))}</td></tr>" for k, v in rows)
            folium.Marker((r.lat, r.lon), popup=folium.Popup(f"<table style='font-size:12px'>{body}</table>",
                                                             max_width=360),
                          icon=folium.Icon(color="red" if r.event_type == "hurricane" else "orange",
                                           icon="building", prefix="fa")).add_to(cluster)

    legend = "".join(f"<div><span style='background:{c};width:12px;height:12px;display:inline-block;margin-right:4px'></span>"
                     f"Hail {lbl}</div>" for _, c, lbl in HAIL_BINS)
    legend += "".join(f"<div><span style='background:{c};opacity:.6;width:12px;height:12px;display:inline-block;"
                      f"margin-right:4px'></span>{lbl}</div>" for c, lbl in WIND.values())
    m.get_root().html.add_child(folium.Element(
        "<div style='position:fixed;bottom:24px;left:12px;z-index:9999;background:#fff;padding:8px 10px;"
        f"border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3);font:12px sans-serif'>{legend}"
        "<div style='margin-top:4px;color:#555'>Dashed outline = radar-only (no SPC report within 10 mi)</div></div>"))
    folium.LayerControl(collapsed=False).add_to(m)
    # frame the parcels if there are any, otherwise the storm footprints
    if len(table):
        lat, lon = table.lat.astype(float), table.lon.astype(float)
        m.fit_bounds([[lat.min(), lon.min()], [lat.max(), lon.max()]], padding=(30, 30), max_zoom=13)
    else:
        layers = [g for g in (fp["hurricane"], fp["hail"]) if len(g)]
        if layers:
            b = [g.total_bounds for g in layers]
            m.fit_bounds([[min(x[1] for x in b), min(x[0] for x in b)], [max(x[3] for x in b), max(x[2] for x in b)]])
    m.save(str(path))

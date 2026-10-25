"""Step 5: CSV outputs, GHL import file, run ledger, map, and the run report."""
from __future__ import annotations

from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd

from .common import OUTPUT, STATE
from .land_use import owner_is_entity

OUT_FIELDS = ["event_type", "event_date", "hail_size_in", "wind_zone_kt", "radar_only_flag", "parcel_id",
              "county", "state", "situs_address", "city", "zip", "land_use_code", "land_use_desc",
              "building_sqft", "year_built", "market_value", "owner_name", "owner_mailing_address",
              "lat", "lon",
              # extras
              "owner_is_entity", "land_use_class", "storm_name", "event_count", "all_events", "match_method",
              "parcel_key", "first_seen", "is_new_this_run", "ghl_pushed", "needs_ghl_push"]

# GHL contact import: column header -> our field. Custom fields must exist in GHL first
# (Settings -> Custom Fields) with these names, or map them during the import wizard.
GHL_MAP = [
    ("First Name", "_first"), ("Last Name", "_last"), ("Company Name", "_company"),
    ("Address1", "_owner_addr1"), ("City", "_owner_city"), ("State", "_owner_state"),
    ("Postal Code", "_owner_zip"), ("Country", "_country"), ("Source", "_source"), ("Tags", "_tags"),
    ("Property Address", "situs_address"), ("Property City", "city"), ("Property Zip", "zip"),
    ("Property County", "county"), ("Property State", "state"), ("Parcel ID", "parcel_id"),
    ("Land Use", "land_use_desc"), ("Land Use Code", "land_use_code"), ("Building SqFt", "building_sqft"),
    ("Year Built", "year_built"), ("Market Value", "market_value"), ("Storm Event", "event_type"),
    ("Storm Name", "storm_name"), ("Event Date", "event_date"), ("Hail Size (in)", "hail_size_in"),
    ("Wind Zone (kt)", "wind_zone_kt"), ("Radar Only", "radar_only_flag"), ("All Storm Events", "all_events"),
    ("Owner Is Entity", "owner_is_entity"), ("Owner Name (raw)", "owner_name"),
    ("Latitude", "lat"), ("Longitude", "lon"), ("Storm Lead Key", "parcel_key"),
]


def _join(*parts) -> str:
    return " ".join(str(p).strip() for p in parts if p is not None and str(p).strip() not in ("", "nan", "None", "<NA>"))


def build_table(hits: gpd.GeoDataFrame) -> pd.DataFrame:
    if hits.empty:
        return pd.DataFrame(columns=OUT_FIELDS)
    df = hits.copy()
    rp = df.geometry.representative_point()
    df["lat"], df["lon"] = rp.y.round(6), rp.x.round(6)
    df["owner_mailing_address"] = [
        ", ".join(x for x in (_join(a1, a2), _join(c), _join(s, z)) if x)
        for a1, a2, c, s, z in zip(df.owner_addr1, df.owner_addr2, df.owner_city, df.owner_state, df.owner_zip)]
    df["owner_is_entity"] = owner_is_entity(df.owner_name)
    df["parcel_key"] = df.state + "|" + df.county + "|" + df.parcel_id.astype(str)
    df["market_value"] = df["market_value"].round(0)
    return pd.DataFrame(df.drop(columns="geometry"))


# ---------------------------------------------------------------- ledger
LEDGER = STATE / "ledger.csv"


def load_ledger() -> pd.DataFrame:
    if LEDGER.exists():
        return pd.read_csv(LEDGER, dtype=str).fillna("")
    return pd.DataFrame(columns=["parcel_key", "event_id", "first_seen", "ghl_pushed_at"])


def update_ledger(table: pd.DataFrame, events: pd.DataFrame, hits_index_to_key: pd.Series, run_ts: str):
    led = load_ledger()
    if events.empty:
        return led, set()
    cur = pd.DataFrame({"parcel_key": hits_index_to_key.loc[events.pidx].values, "event_id": events.event_id.values})
    cur = cur.drop_duplicates()
    known = set(zip(led.parcel_key, led.event_id))
    new = cur[[(k, e) not in known for k, e in zip(cur.parcel_key, cur.event_id)]].copy()
    new["first_seen"], new["ghl_pushed_at"] = run_ts, ""
    led = pd.concat([led, new], ignore_index=True)
    led.to_csv(LEDGER, index=False)
    return led, set(new.parcel_key)


def apply_ledger(table: pd.DataFrame, led: pd.DataFrame, new_keys: set) -> pd.DataFrame:
    if table.empty:
        return table
    g = led.groupby("parcel_key")
    first_seen = g.first_seen.min()
    pushed_at = g.ghl_pushed_at.max()
    # any event row that has not been pushed yet, for a parcel that was pushed before
    unpushed_event = led[led.ghl_pushed_at == ""].groupby("parcel_key").size()
    table["first_seen"] = table.parcel_key.map(first_seen)
    table["is_new_this_run"] = table.parcel_key.isin(new_keys)
    table["ghl_pushed"] = table.parcel_key.map(pushed_at).fillna("").ne("")
    table["needs_ghl_push"] = table.parcel_key.map(unpushed_event).fillna(0).gt(0)
    return table


def mark_pushed(run_ts: str | None = None) -> int:
    """Stamp every un-pushed ledger row as pushed (run after importing output/ghl_import.csv)."""
    led = load_ledger()
    run_ts = run_ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
    m = led.ghl_pushed_at == ""
    led.loc[m, "ghl_pushed_at"] = run_ts
    led.to_csv(LEDGER, index=False)
    master = OUTPUT / "affected_commercial.csv"
    if master.exists():
        t = pd.read_csv(master, dtype=str)
        t["ghl_pushed"], t["needs_ghl_push"] = "True", "False"
        t.to_csv(master, index=False)
    return int(m.sum())


# ---------------------------------------------------------------- GHL
def _split_person(name: str) -> tuple[str, str]:
    """County rolls store 'LAST FIRST MI' (FL DOR). Best effort; joint owners keep the first person."""
    n = str(name or "").split("&")[0].split(" AND ")[0].strip()
    parts = n.replace(",", " ").split()
    if len(parts) < 2:
        return "", n.title()
    return parts[1].title(), parts[0].title()


def ghl_frame(table: pd.DataFrame) -> pd.DataFrame:
    t = table.copy()
    first, last, company = [], [], []
    for nm, ent in zip(t.owner_name.fillna(""), t.owner_is_entity):
        if ent:
            first.append(""), last.append(""), company.append(str(nm).title())
        else:
            f, la = _split_person(nm)
            first.append(f), last.append(la), company.append("")
    t["_first"], t["_last"], t["_company"] = first, last, company
    for src, dst in (("owner_addr1", "_owner_addr1"), ("owner_city", "_owner_city"),
                     ("owner_state", "_owner_state"), ("owner_zip", "_owner_zip")):
        t[dst] = t[src] if src in t else ""
    t["_country"] = "US"
    t["_source"] = "Storm Impact List"
    t["_tags"] = ["storm-lead," + r.event_type + ("," + str(r.storm_name).lower() if r.event_type == "hurricane" else "")
                  + "," + r.county.lower().replace(" ", "-") + "-" + r.state.lower()
                  + (",llc-owner" if r.owner_is_entity else "") for r in t.itertuples()]
    return pd.DataFrame({h: t[c] if c in t else "" for h, c in GHL_MAP})


# ---------------------------------------------------------------- report
def report(table: pd.DataFrame, events: pd.DataFrame, fp: dict) -> str:
    lines = [f"Run {datetime.now():%Y-%m-%d %H:%M}",
             f"Footprints: {len(fp['hail'])} hail swaths over {fp['hail'].event_date.nunique() if len(fp['hail']) else 0} days; "
             f"{fp['hurricane'].storm_id.nunique() if len(fp['hurricane']) else 0} hurricane(s)",
             f"Parcels in output: {len(table)}  (new this run: {int(table.is_new_this_run.sum()) if len(table) else 0}, "
             f"needing GHL push: {int(table.needs_ghl_push.sum()) if len(table) else 0})", "", "By county:"]
    if len(table):
        for (s, c), n in table.groupby(["state", "county"]).size().items():
            lines.append(f"  {c}, {s}: {n}")
        lines += ["", "By event (parcel-event matches):"]
        lab = events.assign(evt=events.apply(
            lambda r: f"hurricane {r.storm_name}" if r.event_type == "hurricane" else f"hail {r.event_date}", axis=1))
        for e, n in lab.groupby("evt").size().sort_index().items():
            lines.append(f"  {e}: {n}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def run(hits: gpd.GeoDataFrame, events: pd.DataFrame, fp: dict, cfg: dict) -> pd.DataFrame:
    from .map import build_map

    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    table = build_table(hits)
    led, new_keys = update_ledger(table, events, table.set_index(hits.index)["parcel_key"] if len(table) else pd.Series(dtype=str), run_ts)
    table = apply_ledger(table, led, new_keys)
    # cumulative master: keep earlier rows that dropped out of the window, replace rows seen again
    master = OUTPUT / "affected_commercial.csv"
    if master.exists() and len(table):
        prev = pd.read_csv(master, dtype=str)
        prev = prev[~prev.parcel_key.isin(table.parcel_key)]
        prev["is_new_this_run"] = "False"
        if len(prev):
            table_out = pd.concat([table.astype(object), prev], ignore_index=True)
        else:
            table_out = table
    else:
        table_out = table
    table_out = table_out.reindex(columns=OUT_FIELDS)
    table_out.to_csv(master, index=False)
    table.reindex(columns=OUT_FIELDS)[table.is_new_this_run.astype(bool)].to_csv(OUTPUT / "new_this_run.csv", index=False) \
        if len(table) else pd.DataFrame(columns=OUT_FIELDS).to_csv(OUTPUT / "new_this_run.csv", index=False)
    need = table[table.needs_ghl_push.astype(bool)] if len(table) else table
    ghl_frame(need).to_csv(OUTPUT / "ghl_import.csv", index=False) if len(need) else \
        pd.DataFrame(columns=[h for h, _ in GHL_MAP]).to_csv(OUTPUT / "ghl_import.csv", index=False)
    pd.DataFrame(GHL_MAP, columns=["ghl_column", "source_field"]).to_csv(OUTPUT / "ghl_column_mapping.csv", index=False)
    if len(events):
        ev = events.copy()
        ev["parcel_key"] = table.set_index(hits.index).loc[ev.pidx, "parcel_key"].values
        ev.drop(columns=["pidx"]).to_csv(OUTPUT / "affected_events.csv", index=False)
    build_map(fp, hits, table, cfg, OUTPUT / "map.html")
    rep = report(table, events, fp)
    (OUTPUT / "run_report.txt").write_text(rep + "\n")
    print("\n" + rep)
    return table_out

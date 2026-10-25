"""Land-use classification: Florida DOR codes and a keyword fallback for other counties."""
from __future__ import annotations

import re

import pandas as pd

FL_DOR = {
    "010": "Vacant commercial", "011": "Stores, one story", "012": "Mixed use (store/office/residential)",
    "013": "Department stores", "014": "Supermarkets", "015": "Regional shopping centers",
    "016": "Community shopping centers", "017": "Office buildings, one story", "018": "Office buildings, multi-story",
    "019": "Professional service buildings", "020": "Airports, terminals, piers, marinas",
    "021": "Restaurants, cafeterias", "022": "Drive-in restaurants", "023": "Financial institutions",
    "024": "Insurance company offices", "025": "Repair service shops, laundries", "026": "Service stations",
    "027": "Auto sales, repair and storage", "028": "Parking lots, mobile home parks",
    "029": "Wholesale outlets, produce houses", "030": "Florists, greenhouses",
    "031": "Drive-in theaters, open stadiums", "032": "Enclosed theaters, auditoriums",
    "033": "Nightclubs, bars", "034": "Bowling alleys, rinks, enclosed arenas", "035": "Tourist attractions",
    "036": "Camps", "037": "Race tracks", "038": "Golf courses, driving ranges", "039": "Hotels, motels",
    "040": "Vacant industrial", "041": "Light manufacturing", "042": "Heavy industrial",
    "043": "Lumber yards, sawmills", "044": "Packing plants", "045": "Canneries, distilleries, wineries",
    "046": "Other food processing", "047": "Mineral processing", "048": "Warehousing, distribution, trucking terminals",
    "049": "Open storage",
}


def norm_dor(code) -> str:
    s = re.sub(r"\D", "", str(code if code is not None else ""))
    return s.zfill(3)[-3:] if s else ""


def classify_fl_dor(codes: pd.Series, cfg: dict) -> pd.Series:
    """-> 'commercial' / 'industrial' / None"""
    lu = cfg["land_use"]
    c = codes.map(norm_dor)
    out = pd.Series(None, index=codes.index, dtype=object)
    out[(c >= lu["fl_commercial"][0]) & (c <= lu["fl_commercial"][1]) & (c != "")] = "commercial"
    out[(c >= lu["fl_industrial"][0]) & (c <= lu["fl_industrial"][1]) & (c != "")] = "industrial"
    if lu.get("exclude_vacant", True):
        out[c.isin(lu.get("fl_vacant_codes", []))] = None
    return out


def classify_keyword(code: pd.Series, desc: pd.Series, cfg: dict) -> pd.Series:
    kw = cfg["keyword_land_use"]
    text = (code.fillna("").astype(str) + " " + desc.fillna("").astype(str)).str.upper()
    inc = text.str.contains("|".join(map(re.escape, kw["include"])), regex=True)
    exc = text.str.contains("|".join(map(re.escape, kw["exclude"])), regex=True)
    ind = text.str.contains(r"INDUST|MANUFACTUR|WAREHOUSE|STORAGE", regex=True)
    out = pd.Series(None, index=code.index, dtype=object)
    out[inc & ~exc] = "commercial"
    out[inc & ~exc & ind] = "industrial"
    return out


_ENTITY = re.compile(
    r"\b(?:LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|LLP|LLLP|LTD|PLLC|PA|PC|TRUST|TR|TRS|TRUSTEE|"
    r"BANK|CHURCH|MINISTR\w*|ASSOC\w*|PARTNERS\w*|PARTNERSHIP|HOLDINGS?|PROPERT\w*|INVEST\w*|GROUP|ENTERPRISES?|"
    r"REALTY|DEVELOPMENT|MANAGEMENT|MGMT|VENTURES?|CAPITAL|FUND|FOUNDATION|UNIVERSITY|COLLEGE|SCHOOL|HOSPITAL|"
    r"CITY OF|COUNTY|STATE OF|BOARD|AUTHORITY|DEPT|DEPARTMENT|UNITED STATES|USA|HOTEL|MOTEL|RESORT|CLUB|"
    r"LEASING|STORAGE|FARMS?|ESTATE OF|INTERNATIONAL|INTL|SERVICES?|SYSTEMS?|INDUSTRIES|PLAZA|CENTER|SHOPPES?)\b"
)


def owner_is_entity(names: pd.Series) -> pd.Series:
    return names.fillna("").astype(str).str.upper().str.contains(_ENTITY)

"""Shared helpers: config, cached HTTP downloads, paths, logging."""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
OUTPUT = ROOT / "output"
STATE = ROOT / "state"

# Equal-area CRS for distance/area math over CONUS
CRS_EQ = "EPSG:5070"
CRS_LL = "EPSG:4326"
MILE_M = 1609.344
NM_M = 1852.0

log = logging.getLogger("storm_leads")
_session = requests.Session()
_session.headers["User-Agent"] = "storm-leads/1.0 (Pinnacle Life Group; contact via repo)"


class NotFound(Exception):
    """Remote file does not exist (404). Cached as a negative result for past dates."""


def load_config(path: Path | None = None, **overrides) -> dict:
    cfg = yaml.safe_load((path or ROOT / "config.yaml").read_text())
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


@dataclass
class Window:
    start: date
    end: date  # inclusive

    def days(self):
        d = self.start
        while d <= self.end:
            yield d
            d += timedelta(days=1)


def make_window(days: int, end: date | None = None) -> Window:
    end = end or date.today()
    return Window(end - timedelta(days=days), end)


def cache_path(url: str, subdir: str) -> Path:
    name = url.rstrip("/").split("/")[-1] or "index"
    if "?" in name or len(name) > 120:
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + "_" + name[:60].replace("?", "_")
    if url.endswith("/"):
        name += ".index.html"
    return RAW / subdir / name


def fetch(url: str, subdir: str, *, refresh: bool = False, negative_cache: bool = True,
          retries: int = 4, timeout: int = 60, offline: bool = False) -> Path:
    """Download `url` into data/raw/<subdir>/ once; later calls return the cached file.

    A 404 writes a `.missing` marker so reruns don't hit the server again
    (pass negative_cache=False for data that may still be published, e.g. today's files).
    """
    dest = cache_path(url, subdir)
    missing = dest.with_name(dest.name + ".missing")
    if dest.exists() and not refresh:
        return dest
    if missing.exists() and not refresh:
        raise NotFound(url)
    if offline:
        raise NotFound(f"offline and not cached: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    delay = 2
    for attempt in range(retries + 1):
        try:
            with _session.get(url, stream=True, timeout=timeout) as r:
                if r.status_code == 404:
                    if negative_cache:
                        missing.touch()
                    raise NotFound(url)
                r.raise_for_status()
                tmp = dest.with_name(dest.name + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(dest)
                return dest
        except NotFound:
            raise
        except requests.RequestException as e:
            if attempt == retries:
                raise
            log.warning("fetch %s failed (%s); retry in %ss", url, e, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def gunzip(path: Path) -> Path:
    if path.suffix != ".gz":
        return path
    out = path.with_suffix("")
    if not out.exists():
        with gzip.open(path, "rb") as fi, open(out, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    return out


def setup_logging(verbose: bool = False):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for p in (RAW, WORK, OUTPUT, STATE):
        p.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYOGRIO_USE_ARROW", "0")

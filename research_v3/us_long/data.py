"""Raw public-data snapshot for the us_long study (network only in `fetch_all`), sha256 manifest, verified loaders.

Sources (as actually used, recorded in manifest.json):
  Ken French data library: F-F_Research_Data_Factors_daily, Portfolios_Formed_on_ME_Daily,
    Developed_ex_US_3_Factors_Daily (zip -> csv). No daily Emerging Markets file exists on the library page.
  FRED fredgraph.csv: DGS10, DGS30, DTB3 (and DGS20, fetched for a sensitivity only).
  Gold: FRED's London fix series are no longer served (error page); GoldPrice.com daily CSV is used
    (LBMA fixes through 2014/15, Metals.dev closes after; CC BY 4.0).
  yfinance: ^IXIC.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST = "manifest.json"
FRENCH = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/{}_CSV.zip"
FRENCH_FILES = {"ff3_daily": "F-F_Research_Data_Factors_daily",
                "size_daily": "Portfolios_Formed_on_ME_Daily",
                "devxus_daily": "Developed_ex_US_3_Factors_Daily"}
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
FRED_IDS = ["DGS10", "DGS30", "DTB3", "DGS20"]
GOLD_URL = "https://goldprice.com/gold-price-history.csv"


class USLongDataError(ValueError):
    pass


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _retry(fn, tries: int = 3):
    last = None
    for _ in range(tries):
        try:
            time.sleep(1.0)
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2.0)
    raise USLongDataError(f"download failed after {tries} tries: {last!r}")


def fetch_all(data_dir: Path = DATA_DIR) -> dict:
    import requests
    import yfinance as yf

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    hdr = {"User-Agent": "Mozilla/5.0"}
    files: dict[str, dict] = {}

    def get(url):
        # FRED hangs on browser User-Agents; only the gold site needs one
        r = requests.get(url, headers=hdr if "goldprice" in url else None, timeout=30)
        r.raise_for_status()
        return r.content

    for key, name in FRENCH_FILES.items():
        url = FRENCH.format(name)
        z = _retry(lambda: get(url))
        with zipfile.ZipFile(io.BytesIO(z)) as zf:
            inner = zf.namelist()[0]
            (data_dir / f"{key}.csv").write_bytes(zf.read(inner))
        files[f"{key}.csv"] = {"source": url, "zip_member": inner}
    for sid in FRED_IDS:
        url = FRED.format(sid)
        b = _retry(lambda: get(url))
        if not b.startswith(b"observation_date"):
            raise USLongDataError(f"FRED {sid} did not return CSV")
        (data_dir / f"fred_{sid}.csv").write_bytes(b)
        files[f"fred_{sid}.csv"] = {"source": url}
    b = _retry(lambda: get(GOLD_URL))
    if not b.startswith(b"date,"):
        raise USLongDataError("gold CSV unexpected")
    (data_dir / "gold_goldprice_com.csv").write_bytes(b)
    files["gold_goldprice_com.csv"] = {"source": GOLD_URL, "note": "FRED GOLDAMGBD228NLBM/GOLDPMGBD228NLBM return an "
                                       "error page; LBMA json blocked by Cloudflare; stooq requires JS challenge"}

    def ix():
        raw = yf.download("^IXIC", start="1970-01-01", end="2006-01-10", auto_adjust=False, progress=False)
        s = raw["Close"]["^IXIC"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        return pd.DataFrame({"Date": s.index.strftime("%Y-%m-%d"), "close": s.values})
    df = _retry(ix)
    df.to_csv(data_dir / "yf_IXIC.csv", index=False)
    files["yf_IXIC.csv"] = {"source": "yfinance ^IXIC Close, 1970-01-01..2006-01-09", "yfinance_version": yf.__version__}

    for f, meta in files.items():
        meta["sha256"] = sha256_file(data_dir / f)
        meta["bytes"] = (data_dir / f).stat().st_size
    man = {"created": pd.Timestamp.now(tz="UTC").isoformat(), "files": files}
    (data_dir / MANIFEST).write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    return man


def verify_manifest(data_dir: Path = DATA_DIR) -> dict:
    data_dir = Path(data_dir)
    man = json.loads((data_dir / MANIFEST).read_text())
    for f, meta in man["files"].items():
        actual = sha256_file(data_dir / f)
        if actual != meta["sha256"]:
            raise USLongDataError(f"{f}: sha256 mismatch (manifest {meta['sha256']}, actual {actual})")
    return man


# ---------------------------------------------------------------- parsers
def _french_sections(path: Path) -> list[tuple[str, pd.DataFrame]]:
    """Parse a French-library CSV into [(title_line, frame)] for each block of YYYYMMDD rows (percent units)."""
    lines = Path(path).read_text(errors="replace").splitlines()
    out, cur, hdr, title, last_text = [], [], None, "", ""
    for ln in lines + [""]:
        parts = [p.strip() for p in ln.split(",")]
        if parts and len(parts[0]) == 8 and parts[0].isdigit():
            cur.append(parts)
        else:
            if cur:
                out.append((title, pd.DataFrame(cur, columns=hdr[: len(cur[0])] if hdr and len(hdr) >= len(cur[0])
                                                else None)))
                cur = []
            if ln.strip():
                if len(parts) > 1 and parts[0] == "":
                    hdr = ["Date"] + parts[1:]
                else:
                    title = ln.strip()
    res = []
    for t, df in out:
        df = df.copy()
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        for c in df.columns[1:]:
            df[c] = pd.to_numeric(df[c])
        df = df.rename(columns={df.columns[0]: "Date"})
        res.append((t, df))
    return res


def load_french_ff3(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    (_, df), = _french_sections(Path(data_dir) / "ff3_daily.csv")[:1]
    return df.set_index("Date")


def load_french_devxus(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    return _french_sections(Path(data_dir) / "devxus_daily.csv")[0][1].set_index("Date")


def load_french_size_lo30_vw(data_dir: Path = DATA_DIR) -> pd.Series:
    secs = _french_sections(Path(data_dir) / "size_daily.csv")
    df = secs[0][1].set_index("Date")   # first block = value-weighted returns
    return df["Lo 30"]


def load_fred(sid: str, data_dir: Path = DATA_DIR) -> pd.Series:
    df = pd.read_csv(Path(data_dir) / f"fred_{sid}.csv")
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = df.iloc[:, 0].astype(str).values
    return s.dropna()


def load_gold(data_dir: Path = DATA_DIR) -> pd.Series:
    df = pd.read_csv(Path(data_dir) / "gold_goldprice_com.csv", comment="#")
    s = pd.Series(df.iloc[:, 1].astype(float).values, index=df.iloc[:, 0].astype(str).values)
    return s[s > 0]


def load_ixic(data_dir: Path = DATA_DIR) -> pd.Series:
    df = pd.read_csv(Path(data_dir) / "yf_IXIC.csv")
    return pd.Series(df["close"].astype(float).values, index=df["Date"].astype(str).values).dropna()

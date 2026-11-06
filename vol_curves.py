"""
vol_curves.py — US and Japan index implied-vol term structures for the put vol screen.

US: CBOE VIX family off Yahoo (^VIX9D, ^VIX, ^VIX3M, ^VIX6M, ^VIX1Y) -> fixed tenors 9/30/93/182/365d.
JP: Nikkei 225 index options, ATM vol per listed expiry from JPX's daily settlement CSV
    (rb_eYYYYMMDD.csv, the same file that carries OSE settlement vols), then resampled to
    constant maturities by total-variance interpolation (same method as the per-name 90d IV).

Each run appends today's curve to a small JSON store so the page can overlay 1w / 1m ago.

Integration (in the generator, before the page is written):
    from vol_curves import build_curves
    D["curves"] = build_curves("curves_store.json")
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (put-vol-screen)"}
US_TICKERS = [("^VIX9D", 9), ("^VIX", 30), ("^VIX3M", 93), ("^VIX6M", 182), ("^VIX1Y", 365)]
CM_TENORS = [30, 60, 90, 180, 365]            # constant-maturity points shown for JP
JPX_URL = ("https://www.jpx.co.jp/english/markets/derivatives/settlement-price/"
           "tvdivq00000014l6-att/rb_e{d}.csv")
N225_RE = re.compile(r"^(CAL|PUT)_225_(\d{6})_(\d+(?:\.\d+)?)$")   # index options only (not mini/weekly)
MIN_DTE = 5                                   # drop expiries this close: pin/gamma noise
HISTORY_KEEP = 400                            # days of curves kept in the store


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- US
def _yahoo_last(ticker: str):
    import yfinance as yf
    h = yf.Ticker(ticker).history(period="10d")["Close"].dropna()
    if h.empty:
        return None, None
    return h.index[-1].date().isoformat(), float(h.iloc[-1]) / 100.0


def us_curve():
    pts, asof = [], None
    for tk, days in US_TICKERS:
        try:
            d, v = _yahoo_last(tk)
        except Exception as e:                 # one missing index shouldn't kill the curve
            print(f"[vol_curves] US {tk}: {e}")
            continue
        if v is not None:
            pts.append({"t": days, "v": round(v, 4), "src": tk})
            asof = max(asof or d, d)
    return {"name": "S&P 500 (VIX term structure)", "asof": asof, "pts": pts} if pts else None


# ---------------------------------------------------------------- JP
def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _atm_vol(rows, fwd):
    """Vol at K = underlying by linear interpolation in strike, put and call averaged."""
    out = []
    for side in ("CAL", "PUT"):
        s = sorted((k, v) for c, k, v in rows if c == side and v and v > 0)
        lo = [p for p in s if p[0] <= fwd]
        hi = [p for p in s if p[0] >= fwd]
        if not lo or not hi:
            continue
        (k1, v1), (k2, v2) = lo[-1], hi[0]
        out.append(v1 if k2 == k1 else v1 + (v2 - v1) * (fwd - k1) / (k2 - k1))
    return sum(out) / len(out) if out else None


def parse_jpx(raw: bytes):
    """Return [(dte, atm_vol)] for Nikkei 225 index options from one rb_e CSV."""
    text = raw.decode("cp932", errors="replace")
    by_exp: dict[str, dict] = {}
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 11:
            continue
        m = N225_RE.match(r[1].strip())
        if not m:
            continue
        side, exp, _ = m.groups()
        k, vol, und, dte = _num(r[4]), _num(r[8]), _num(r[7]), _num(r[10])
        if None in (k, vol, und, dte):
            continue
        e = by_exp.setdefault(exp, {"und": und, "dte": dte, "rows": []})
        e["rows"].append((side, k, vol / 100.0))
    pts = []
    for exp, e in by_exp.items():
        if e["dte"] < MIN_DTE:
            continue
        v = _atm_vol(e["rows"], e["und"])
        if v:
            pts.append((int(e["dte"]), v))
    return sorted(pts)


def cm_interp(pts, tenors=CM_TENORS):
    """Constant-maturity vols by linear interpolation of total variance (sigma^2 * T).
    Flat vol beyond the ends rather than extrapolating the slope."""
    out = []
    for T in tenors:
        lo = [p for p in pts if p[0] <= T]
        hi = [p for p in pts if p[0] >= T]
        if lo and hi:
            (t1, v1), (t2, v2) = lo[-1], hi[0]
            if t1 == t2:
                v = v1
            else:
                w1, w2 = v1 * v1 * t1, v2 * v2 * t2
                v = math.sqrt((w1 + (w2 - w1) * (T - t1) / (t2 - t1)) / T)
            out.append({"t": T, "v": round(v, 4)})
        elif lo and T <= lo[-1][0] * 1.5:      # short extrapolation only
            out.append({"t": T, "v": round(lo[-1][1], 4), "x": 1})
        elif hi and not lo:
            out.append({"t": T, "v": round(hi[0][1], 4), "x": 1})
    return out


def jp_curve(today: dt.date | None = None, lookback: int = 6, raw: bytes | None = None):
    if raw is not None and today is not None:
        pts = parse_jpx(raw)
        if pts:
            return {"name": "Nikkei 225 (index options, ATM)", "asof": today.isoformat(),
                    "pts": cm_interp(pts), "raw": [{"t": t, "v": round(v, 4)} for t, v in pts]}
    d = today or dt.date.today()
    for _ in range(lookback):                  # walk back over weekends / holidays / pre-4:45pm
        try:
            pts = parse_jpx(_get(JPX_URL.format(d=d.strftime("%Y%m%d"))))
        except Exception:
            pts = []
        if pts:
            return {"name": "Nikkei 225 (index options, ATM)", "asof": d.isoformat(),
                    "pts": cm_interp(pts),
                    "raw": [{"t": t, "v": round(v, 4)} for t, v in pts]}
        d -= dt.timedelta(days=1)
    print("[vol_curves] JP: no JPX settlement file found")
    return None


# ---------------------------------------------------------------- store + page payload
def _ago(hist: dict, asof: str, days: int):
    """Latest stored curve on or before asof - days."""
    cut = (dt.date.fromisoformat(asof) - dt.timedelta(days=days)).isoformat()
    ks = [k for k in hist if k <= cut]
    return {"asof": max(ks), "pts": hist[max(ks)]} if ks else None


def build_curves(store_path: str = "curves_store.json"):
    store = {"US": {}, "JP": {}}
    if os.path.exists(store_path):
        with open(store_path) as f:
            store.update(json.load(f))
    out = build_curves_from(store)
    with open(store_path, "w") as f:
        json.dump(store, f, separators=(",", ":"))
    return out


def build_curves_from(store: dict, jpx_raw: bytes | None = None, jpx_date: dt.date | None = None):
    """Same as build_curves but on an in-memory store dict (mutated in place)."""
    out = {}
    for mkt, fn in (("US", us_curve), ("JP", lambda: jp_curve(jpx_date, raw=jpx_raw))):
        try:
            c = fn()
        except Exception as e:
            print(f"[vol_curves] {mkt}: {e}")
            c = None
        hist = store.setdefault(mkt, {})
        if c and c["asof"]:
            hist[c["asof"]] = c["pts"]
        if len(hist) > HISTORY_KEEP:
            for k in sorted(hist)[:-HISTORY_KEEP]:
                del hist[k]
        if not c and hist:                     # fetch failed: show last stored curve, marked stale
            last = max(hist)
            c = {"name": mkt, "asof": last, "pts": hist[last], "stale": 1}
        if c:
            c["w1"] = _ago(hist, c["asof"], 7)
            c["m1"] = _ago(hist, c["asof"], 30)
            out[mkt] = c
    return out


if __name__ == "__main__":
    print(json.dumps(build_curves(), indent=1))

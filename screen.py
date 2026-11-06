"""
Put vol screen — daily build. Plain Python, no AI in the loop; run by .github/workflows/daily.yml.

Per name: 90d / 180d constant-maturity ATM implied vol (total-variance interpolation between
listed expiries), 20d/60d realized vol, IV percentile vs the name's own stored history, RV
percentile vs 2y, earnings flag, quality grade. Plus index gauges and US/JP vol curves.

Sources (all free):
  US  options + prices + earnings ... Yahoo via yfinance (IV solved from OTM mids, Black-Scholes)
  JP  options ........................ JPX daily settlement CSV (exchange settlement vol)
  HK  options ........................ HKEX stock options daily market report (IV% per series)
  JP/HK prices + earnings ............ Yahoo via yfinance
  Gauges ............................. VIX (Yahoo), Nikkei VI (Nikkei Indexes), HSI 30d ATM
                                       from HKEX index options report (VHSI proxy)
Usage: python screen.py            -> writes index.html, data/store.json
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys
import time
import traceback
import urllib.request
from statistics import NormalDist

import numpy as np
import pandas as pd
import yfinance as yf

from vol_curves import (JPX_URL, N225_RE, build_curves_from, cm_interp, parse_jpx)

TENOR, TENOR2, MINOBS = 90, 180, 60
RATE = {"US": 0.04, "JP": 0.01, "HK": 0.03}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
N = NormalDist()
TODAY = dt.date.today()
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a); LOG.append(s); print(s, flush=True)


def get(url: str, timeout=60) -> bytes:
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            time.sleep(3 * (k + 1))
        except Exception:
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"fetch failed: {url}")


# ------------------------------------------------------------------ vol maths
def bs(S, K, T, r, sig, call):
    if sig <= 0 or T <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T)); d2 = d1 - sig * math.sqrt(T)
    return S * N.cdf(d1) - K * math.exp(-r * T) * N.cdf(d2) if call else K * math.exp(-r * T) * N.cdf(-d2) - S * N.cdf(-d1)


def implied(price, S, K, T, r, call):
    intrinsic = max(0.0, (S - K * math.exp(-r * T)) if call else (K * math.exp(-r * T) - S))
    if price is None or not price > intrinsic + 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    if bs(S, K, T, r, hi, call) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs(S, K, T, r, mid, call) > price: hi = mid
        else: lo = mid
    return (lo + hi) / 2


def atm_from_strikes(pts, spot):
    """pts: [(K, vol)] -> vol at K=spot, linear in strike; None if spot not bracketed."""
    pts = sorted(p for p in pts if p[1])
    lo = [p for p in pts if p[0] <= spot]; hi = [p for p in pts if p[0] >= spot]
    if not lo or not hi:
        return None
    (k1, v1), (k2, v2) = lo[-1], hi[0]
    return v1 if k2 == k1 else v1 + (v2 - v1) * (spot - k1) / (k2 - k1)


def cm_vol(exp_pts, T):
    """exp_pts: [(dte, vol)]. Constant-maturity vol at T days by total-variance interpolation.
    Returns (vol, how)."""
    p = sorted(x for x in exp_pts if x[1] and x[0] > 0)
    if not p:
        return None, "none"
    lo = [x for x in p if x[0] <= T]; hi = [x for x in p if x[0] >= T]
    if lo and hi:
        (t1, v1), (t2, v2) = lo[-1], hi[0]
        if t1 == t2:
            return v1, "interp"
        w = v1 * v1 * t1 + (v2 * v2 * t2 - v1 * v1 * t1) * (T - t1) / (t2 - t1)
        return (math.sqrt(w / T) if w > 0 else None), "interp"
    if lo:
        return lo[-1][1], f"extrap_short (last {lo[-1][0]}d)"
    return hi[0][1], f"extrap_long (first {hi[0][0]}d)"


# ------------------------------------------------------------------ US options (Yahoo)
def us_options(tk: str, spot: float):
    t = yf.Ticker(tk)
    exps = list(t.options or [])
    if not exps:
        return None
    dte = [((dt.date.fromisoformat(e) - TODAY).days, e) for e in exps]
    dte = [x for x in dte if x[0] >= 7]
    pick = set()
    for T in (TENOR, TENOR2):
        lo = [x for x in dte if x[0] <= T]; hi = [x for x in dte if x[0] >= T]
        if lo: pick.add(lo[-1])
        if hi: pick.add(hi[0])
    if not pick and dte:
        pick.add(dte[-1])
    out, quoted = [], True
    for days, e in sorted(pick):
        try:
            ch = t.option_chain(e)
        except Exception as ex:
            log(f"  {tk} chain {e}: {ex}"); continue
        T = days / 365.0
        ks = sorted(set(ch.calls["strike"]) & set(ch.puts["strike"]))
        k_lo = max([k for k in ks if k <= spot], default=None); k_hi = min([k for k in ks if k >= spot], default=None)
        if k_lo is None or k_hi is None:
            continue
        pts, allq = [], True
        for k in {k_lo, k_hi}:
            vs = []
            for df, call in ((ch.calls, True), (ch.puts, False)):
                r = df[df["strike"] == k].iloc[0]
                bid, ask, last = float(r.get("bid") or 0), float(r.get("ask") or 0), float(r.get("lastPrice") or 0)
                q = bid > 0 and ask >= bid
                v = implied((bid + ask) / 2 if q else last, spot, k, T, RATE["US"], call)
                if v is None and r.get("impliedVolatility") and r["impliedVolatility"] > 0.01:
                    v, q = float(r["impliedVolatility"]), False
                if v:
                    vs.append(v); allq = allq and q
            if vs:
                pts.append((k, sum(vs) / len(vs)))
        av = atm_from_strikes(pts, spot)
        quoted = quoted and allq
        if av:
            out.append((days, av))
        time.sleep(0.2)
    return {"pts": out, "q": "A" if quoted else "B"} if out else None


# ------------------------------------------------------------------ JP options (JPX)
def jpx_file():
    d = TODAY
    for _ in range(7):
        try:
            raw = get(JPX_URL.format(d=d.strftime("%Y%m%d")))
            if raw:
                return d, raw
        except Exception:
            pass
        d -= dt.timedelta(days=1)
    return None, None


def jp_options(raw: bytes):
    """{code: [(dte, atm_vol)]} for single-stock options."""
    text = raw.decode("cp932", errors="replace")
    by = {}
    for line in text.splitlines():
        r = line.split(",")
        if len(r) < 11:
            continue
        m = re.match(r"^(CAL|PUT)_([0-9A-Z]{4})_(\d{6})_", r[1].strip())
        if not m or m.group(2) in ("225", "TPX"):
            continue
        try:
            k, vol, und, dte = float(r[4]), float(r[8]), float(r[7]), float(r[10])
        except ValueError:
            continue
        e = by.setdefault(m.group(2), {}).setdefault(m.group(3), {"und": und, "dte": dte, "pts": []})
        e["pts"].append((k, vol / 100.0))
    out = {}
    for code, exps in by.items():
        pts = []
        for e in exps.values():
            if e["dte"] < 7:
                continue
            v = atm_from_strikes(e["pts"], e["und"])
            if v:
                pts.append((int(e["dte"]), v))
        if pts:
            out[code] = sorted(pts)
    return out


# ------------------------------------------------------------------ HK options (HKEX)
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
ROW = re.compile(r"^\s*(\d{2})([A-Z]{3})(\d{2})\s+([\d.]+)\s+([CP])\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s+(\d+)\s+([\d,]+)")


def hkex_file():
    d = TODAY
    for _ in range(7):
        try:
            raw = get(f"https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/dqe{d.strftime('%y%m%d')}.htm", timeout=120)
            return d, raw.decode("latin1")
        except Exception:
            d -= dt.timedelta(days=1)
    return None, None


def hk_options(text: str, report_date: dt.date, code: str):
    """ATM vols per expiry for HK stock code (e.g. '3750'); None if no option class."""
    m = re.search(r"^([A-Z0-9]{3})\s.{0,40}\(0*%s\)" % int(code), text, re.M)
    if not m:
        return None
    cls = m.group(1)
    a = text.find(f'NAME="{cls}"')
    if a < 0:
        return None
    b = text.find('NAME="', a + 10)
    sec = text[a:b if b > 0 else None]
    cp = re.search(r"CLOSING PRICE HK\$\s*([\d.,]+)", sec)
    if not cp:
        return None
    spot = float(cp.group(1).replace(",", ""))
    exps = {}
    for line in sec.splitlines():
        r = ROW.match(line)
        if not r:
            continue
        day, mon, yy, k, side = r.group(1), r.group(2), r.group(3), float(r.group(4)), r.group(5)
        iv, vol = int(r.group(11)), int(r.group(12).replace(",", ""))
        if iv <= 0:
            continue
        exp = dt.date(2000 + int(yy), MON[mon], int(day))
        exps.setdefault(exp, []).append((k, iv / 100.0, vol, side))
    pts, traded = [], False
    for exp, rows in exps.items():
        dte = (exp - report_date).days
        if dte < 7:
            continue
        v = atm_from_strikes([(k, iv) for k, iv, _, _ in rows], spot)
        if v:
            pts.append((dte, v))
            ks = sorted({k for k, *_ in rows})
            near = {max([k for k in ks if k <= spot], default=None), min([k for k in ks if k >= spot], default=None)}
            traded = traded or any(vol > 0 for k, _, vol, _ in rows if k in near)
    return {"pts": sorted(pts), "q": "A" if traded else "B", "cls": cls} if pts else None


# ------------------------------------------------------------------ gauges
def g_vix():
    h = yf.Ticker("^VIX").history(period="10d")["Close"].dropna()
    return h.index[-1].date().isoformat(), float(h.iloc[-1]) / 100


def g_nikkei_vi():
    j = json.loads(get("https://indexes.nikkei.co.jp/en/nkave/get_real_data?idx=nk225vi"))
    d = dt.datetime.strptime(j["datedtime"][:11], "%b/%d/%Y").date()
    return d.isoformat(), float(j["price"]) / 100


def g_hsi30():
    """HSI options 30d ATM vol from HKEX daily report — stand-in for VHSI (no free VHSI feed)."""
    d = TODAY
    for _ in range(7):
        try:
            t = get(f"https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/hsio{d.strftime('%y%m%d')}.htm").decode("latin1")
            break
        except Exception:
            d -= dt.timedelta(days=1)
    else:
        return None, None
    und = re.search(r"(?:CLOSING|UNDERLYING)[^\n]*?([\d,]{5,}\.?\d*)", t)
    spot = float(und.group(1).replace(",", "")) if und else None
    rows = {}
    for line in t.splitlines():
        m = re.match(r"^\s*([A-Z]{3})-?(\d{2})\s+([\d,]+)\s+([CP])\s+(.*)$", line)
        if not m:
            continue
        nums = re.findall(r"[-+]?[\d,]*\.?\d+", m.group(5))
        ivs = [int(x) for x in nums if re.fullmatch(r"\d{1,3}", x)]
        if not ivs:
            continue
        exp = dt.date(2000 + int(m.group(2)), MON[m.group(1)], 28)   # month-end proxy (HSI options expire ~2nd last bday)
        rows.setdefault(exp, []).append((float(m.group(3).replace(",", "")), ivs[0] / 100.0))
    if not spot or not rows:
        log("  HSI report parse: spot", spot, "expiries", len(rows))
        i = t.find("CONTRACT"); log(t[max(0, i - 1500):i + 3000]); return None, None
    pts = [((e - d).days, atm_from_strikes(r, spot)) for e, r in rows.items() if (e - d).days >= 5]
    v, _ = cm_vol([p for p in pts if p[1]], 30)
    return d.isoformat(), v


# ------------------------------------------------------------------ prices / RV / earnings
def yf_sym(m, t):
    if m == "JP":
        return f"{t}.T"
    if m == "HK":
        return f"{int(t):04d}.HK"
    return t


def rv_block(close: pd.Series):
    lr = np.log(close).diff().dropna()
    rv20 = lr.rolling(20).std() * math.sqrt(252)
    rv60 = lr.rolling(60).std() * math.sqrt(252)
    s = rv20.dropna().iloc[-504:]
    cur = float(s.iloc[-1])
    pct = float((s < cur).mean() * 100 + (s == cur).mean() * 50)
    tail = rv20.dropna().iloc[-252:]
    return cur, float(rv60.dropna().iloc[-1]), pct, [d.date().isoformat() for d in tail.index], [round(float(x), 4) for x in tail]


def earnings_days(sym):
    try:
        c = yf.Ticker(sym).calendar or {}
        ds = c.get("Earnings Date") or []
        fut = [(d - TODAY).days for d in ds if isinstance(d, dt.date) and (d - TODAY).days >= 0]
        return min(fut) if fut else None
    except Exception:
        return None


def pctile(hist: dict, today_v):
    vals = [v for v in hist.values() if v is not None]
    if len(vals) < MINOBS or today_v is None:
        return None
    a = np.array(vals)
    return float((a < today_v).mean() * 100 + (a == today_v).mean() * 50)


# ------------------------------------------------------------------ main
def main():
    store = json.load(open("data/store.json"))
    uni = json.load(open("universe.json"))
    syms = [yf_sym(u["m"], u["t"]) for u in uni]
    log(f"downloading prices for {len(syms)} names")
    px = yf.download(syms, period="3y", auto_adjust=True, progress=False, threads=True, group_by="ticker")

    jd, jraw = jpx_file(); jp = jp_options(jraw) if jraw else {}
    log(f"JPX file {jd}: {len(jp)} single-stock underlyings")
    hd, htext = hkex_file(); log(f"HKEX report {hd}: {'ok' if htext else 'missing'}")

    rows = []
    for u, sym in zip(uni, syms):
        m, t = u["m"], u["t"]; key = f"{m}|{t}"
        try:
            close = px[sym]["Close"].dropna() if sym in px.columns.get_level_values(0) else pd.Series(dtype=float)
            if len(close) < 30:
                log(f"{key}: no prices"); continue
            spot = float(close.iloc[-1]); asof = close.index[-1].date().isoformat()
            rv20, rv60, rvp, hd_, hrv = rv_block(close)
            opt, q = None, "RV"
            if m == "US":
                opt = us_options(sym, spot)
            elif m == "JP" and t in jp:
                opt = {"pts": jp[t], "q": "B"}; asof = jd.isoformat()
            elif m == "HK" and htext:
                opt = hk_options(htext, hd, t)
                if opt: asof = hd.isoformat()
            iv = iv180 = None; how = "none"
            if opt:
                iv, how = cm_vol(opt["pts"], TENOR); iv180, _ = cm_vol(opt["pts"], TENOR2); q = opt["q"]
            h = store["iv"].setdefault(key, {})
            if iv is not None:
                last = [h[k] for k in sorted(h)[-4:]]
                if len(last) >= 3 and all(abs(x - iv) < 1e-6 for x in last):
                    q = "C"                                   # unchanged for several days: stale source
                h[asof] = round(iv, 4)
            ivd = sorted(h)
            rows.append({"m": m, "t": t, "n": u["n"], "asof": asof,
                         "iv": iv, "iv180": iv180, "rv20": rv20, "rv60": rv60,
                         "ivrv": (iv / rv20) if iv and rv20 else None,
                         "ivp": pctile(h, iv), "ivn": len(h), "rvp": rvp, "q": q, "how": how,
                         "earn": earnings_days(sym),
                         "hist": {"d": hd_, "rv": hrv, "ivd": ivd, "iv": [h[k] for k in ivd]}})
            log(f"{key}: iv={iv and round(iv,3)} q={q} {how} pts={opt and len(opt['pts'])}")
        except Exception:
            log(f"{key}: ERROR"); traceback.print_exc()

    gauges = []
    for name, fn in (("Nikkei VI", g_nikkei_vi), ("VHSI", g_hsi30), ("VIX", g_vix)):
        try:
            d, v = fn()
        except Exception as e:
            log(f"gauge {name}: {e}"); d, v = None, None
        gh = store["gauges"].setdefault(name, {})
        if v is not None:
            gh[d] = round(v, 4)
        if gh:
            d = max(gh); v = gh[d]
        label = "HSI 30d ATM" if name == "VHSI" else name
        gauges.append({"name": label, "v": v, "asof": d, "n": len(gh), "pct": pctile(gh, v)})

    curves = build_curves_from(store["curves"], jpx_raw=jraw, jpx_date=jd)

    D = {"rows": rows, "gauges": gauges, "curves": curves,
         "built": dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
         "minobs": MINOBS, "tenor": TENOR}
    tpl = open("template.html", encoding="utf-8").read()
    open("index.html", "w", encoding="utf-8").write(tpl.replace("/*DATA*/", json.dumps(D, separators=(",", ":")), 1))
    json.dump(store, open("data/store.json", "w"), separators=(",", ":"))
    ok = sum(r["iv"] is not None for r in rows)
    log(f"built: {len(rows)} names, {ok} with IV")
    if len(rows) < len(uni) * 0.8 or ok < len(rows) * 0.5:
        sys.exit("too many failures — not publishing")


if __name__ == "__main__":
    main()

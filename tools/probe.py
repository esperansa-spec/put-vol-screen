import datetime as dt, urllib.request, re, traceback
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40) as r:
        return r.status, r.read()
def sec(t): print("\n=====", t, flush=True)
D = dt.date.today()

def jpx():
    for i in range(6):
        ds = (D - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            st, b = get(f"https://www.jpx.co.jp/english/markets/derivatives/settlement-price/tvdivq00000014l6-att/rb_e{ds}.csv")
        except Exception as e:
            print(ds, e); continue
        L = [l.split(",") for l in b.decode("cp932", "replace").splitlines()]
        print(ds, st, len(L)); print(L[:3])
        for pat in ["_225_", "_7974_", "_8001_", "_6857_", "_8058_", "_8766_", "7974", "NINTENDO"]:
            m = [l for l in L if len(l) > 2 and (pat in l[1] or pat in ",".join(l))]
            print(pat, len(m), m[:3])
        pref = {}
        for l in L:
            if len(l) > 2:
                k = "_".join(l[1].split("_")[:2]); pref[k] = pref.get(k, 0) + 1
        print("prefixes", sorted(pref.items(), key=lambda x: -x[1])[:120])
        return

def jpx_opt_page():
    st, b = get("https://www.jpx.co.jp/english/markets/derivatives/option-price/index.html")
    print(st, re.findall(r'href="([^"]+)"', b.decode("utf8", "replace"))[:200])

def hkex():
    for i in range(7):
        ds = (D - dt.timedelta(days=i)).strftime("%y%m%d")
        u = f"https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/dqe{ds}.htm"
        try:
            st, b = get(u)
        except Exception as e:
            print(u, e); continue
        t = b.decode("utf8", "replace"); print(u, st, len(t)); print(t[:2500])
        for k in ["CATL", "CTL", "CONTEMPORARY", "LENOVO", "LEN ", "SANHUA", "INNOLIGHT", "CAMBRIDGE"]:
            p = t.upper().find(k); print("--", k, p)
            if p >= 0: print(t[max(0, p - 300):p + 3000])
        return

def nikkei_vi():
    for u in ["https://indexes.nikkei.co.jp/en/nkave/index/profile?idx=nk225vi",
              "https://indexes.nikkei.co.jp/en/nkave/get_real_data?idx=nk225vi",
              "https://indexes.nikkei.co.jp/en/nkave/archives/data?list=vi"]:
        try:
            st, b = get(u); t = b.decode("utf8", "replace"); print(u, st, len(t))
            for m in re.finditer(r"(?i)(current|value|price)[^<]{0,200}", t):
                print(m.group(0)[:200]); break
            print(t[:800])
        except Exception as e: print(u, e)

def yf_bits():
    import yfinance as yf
    for s in ["^VIX", "^VIX9D", "^VIX3M", "^VIX6M", "^VIX1Y", "^VHSI", "^JNIV", "^N225", "7974.T", "0992.HK", "3750.HK"]:
        try:
            h = yf.Ticker(s).history(period="5d"); print(s, len(h), h["Close"].tail(2).to_dict())
        except Exception as e: print(s, e)
    t = yf.Ticker("AAPL"); ex = t.options; print("AAPL expiries", ex[:12])
    c = t.option_chain(ex[min(4, len(ex) - 1)]); print(c.calls.columns.tolist()); print(c.calls.iloc[len(c.calls)//2-2:len(c.calls)//2+2].to_string())
    try: print("cal", t.calendar)
    except Exception as e: print("cal", e)
    try: print("ed", t.get_earnings_dates(limit=4))
    except Exception as e: print("ed", e)
    for s in ["7974.T", "0992.HK"]:
        try: print(s, "options", yf.Ticker(s).options[:5])
        except Exception as e: print(s, e)

for name, f in [("JPX rb_e", jpx), ("JPX option-price page", jpx_opt_page), ("HKEX dqe", hkex),
                ("Nikkei VI", nikkei_vi), ("yfinance", yf_bits)]:
    sec(name)
    try: f()
    except Exception: traceback.print_exc()

import datetime as dt, urllib.request, re, traceback, json
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
        return r.status, r.read()
def sec(t): print("\n=====", t, flush=True)

def hkex_detail():
    st, b = get("https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/dqe261105.htm")
    t = b.decode("latin1")
    p = t.find("SUMMARY", 3000); print(t[p - 200:p + 2500])
    for k in ["CAT", "LEN"]:
        a = t.find(f'NAME="{k}"');
        if a < 0: a = t.find(f'name="{k}"')
        print("\n---", k, a); print(t[a:a + 9000])

def vhsi():
    for u in ["https://www.hsi.com.hk/data/eng/rt/index-series/vhsi/performance.do",
              "https://www.hsi.com.hk/data/eng/rt/index-series/hsi-volatility/performance.do",
              "https://www.hsi.com.hk/eng/indexes/all-indexes/vhsi",
              "https://www.hsi.com.hk/data/eng/indexes/indexes-list.do",
              "https://query1.finance.yahoo.com/v1/finance/search?q=VHSI",
              "https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/hsio261105.htm",
              "https://www.hkex.com.hk/eng/stat/dmstat/dayrpt/vhsif261105.htm"]:
        try:
            st, b = get(u); t = b.decode("utf8", "replace"); print(u, st, len(t))
            i = t.upper().find("VHSI"); print(t[:600] if i < 0 else t[max(0, i - 300):i + 1200])
        except Exception as e: print(u, e)

for name, f in [("HKEX detail", hkex_detail), ("VHSI", vhsi)]:
    sec(name)
    try: f()
    except Exception: traceback.print_exc()

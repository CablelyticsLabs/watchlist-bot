"""
universe.py
───────────
Builds the scanning universe from free public sources.

Pool logic:
  - MAINSTREAM = S&P 500 + NASDAQ 100 + config mainstream list
  - HIDDEN GEM CANDIDATES = Russell 1000 tickers NOT in S&P 500/NASDAQ

At scoring time, a Russell-only ticker is tagged as a hidden gem IF:
  - composite_score >= 65
  - social_mentions < 5 in the last 24 hours
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import List, Set, Tuple

import requests

log = logging.getLogger(__name__)

CACHE_FILE = Path("data/universe_cache.json")
CACHE_MAX_AGE_HOURS = 24


def get_universe(config: dict) -> Tuple[List[str], Set[str]]:
    """
    Returns (all_tickers, russell_only_set).
    russell_only_set = tickers in Russell 1000 but NOT in S&P 500 or NASDAQ 100.
    These are the hidden gem candidates.
    """
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            age_hours = (time.time() - cached.get("timestamp", 0)) / 3600
            if age_hours < CACHE_MAX_AGE_HOURS and len(cached.get("tickers", [])) > 100:
                log.info("Universe from cache: %d tickers, %d gem candidates (%.1fh old)",
                         len(cached["tickers"]), len(cached.get("russell_only", [])), age_hours)
                return cached["tickers"], set(cached.get("russell_only", []))
        except Exception:
            pass

    log.info("Building fresh universe...")
    tickers, russell_only = _build_universe(config)

    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "timestamp": time.time(),
        "tickers": tickers,
        "russell_only": list(russell_only),
    }))
    log.info("Universe: %d total | %d gem candidates", len(tickers), len(russell_only))
    return tickers, russell_only


def _build_universe(config: dict) -> Tuple[List[str], Set[str]]:
    seen: Set[str] = set()
    result: List[str] = []

    def add(tickers: List[str], source: str) -> Set[str]:
        added = set()
        for t in tickers:
            t = t.strip().upper()
            if not t or len(t) > 6 or "." in t or "/" in t:
                continue
            if t not in seen:
                seen.add(t)
                result.append(t)
                added.add(t)
        log.info("[Universe] %-30s +%d (total: %d)", source, len(added), len(result))
        return added

    sp500_set   = add(_sp500(),    "S&P 500")
    nasdaq_set  = add(_nasdaq100(), "NASDAQ 100")
    russell_set = add(_russell1000(), "Russell 1000")
    add(config.get("universe", {}).get("mainstream", []), "Mainstream (config)")
    add(_yfinance_screeners(), "yfinance Screeners")

    # Hidden gem candidates = Russell only, not already in S&P or NASDAQ
    russell_only = russell_set - sp500_set - nasdaq_set
    log.info("Hidden gem candidates (Russell excl. S&P/NASDAQ): %d", len(russell_only))

    random.shuffle(result)
    return result, russell_only


def _sp500() -> List[str]:
    """Pull S&P 500 from iShares IVV ETF holdings CSV — works in GitHub Actions."""
    try:
        import pandas as pd
        from io import StringIO
        url = ("https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
               "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            start = next((i for i, l in enumerate(lines) if "Ticker" in l or "ticker" in l), 0)
            df = pd.read_csv(StringIO("\n".join(lines[start:])))
            col = next((c for c in df.columns if "ticker" in c.lower()), None)
            if col:
                tickers = [t for t in df[col].dropna().tolist()
                           if isinstance(t, str) and t.isalpha() and len(t) <= 5]
                log.info("S&P 500: fetched %d tickers from iShares IVV", len(tickers))
                return tickers
    except Exception as e:
        log.warning("S&P 500 iShares fetch failed: %s — using fallback", e)

    # Fallback: hardcoded S&P 500 core tickers
    return [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","BRK.B","LLY","AVGO",
        "TSLA","JPM","UNH","V","XOM","MA","COST","HD","PG","JNJ","ABBV","MRK",
        "CVX","CRM","BAC","NFLX","AMD","PEP","KO","TMO","WMT","CSCO","ACN","MCD",
        "ABT","LIN","TXN","PM","DHR","NEE","RTX","T","AMGN","INTU","SPGI","HON",
        "UNP","ISRG","CAT","GE","BKNG","PFE","LOW","COP","VRTX","DE","MDT","AXP",
        "AMAT","GILD","ETN","SYK","ADI","PANW","REGN","BLK","CB","LRCX","SCHW",
        "NOW","ZTS","MO","ELV","BSX","MDLZ","MMC","PLD","DUK","SO","CI","SLB",
        "AON","CME","TJX","WM","ITW","NOC","GD","ICE","HUM","PGR","MCO","APH",
        "NSC","EMR","KLAC","FCX","USB","TGT","ECL","PSA","FDX","OXY","AIG","GWW",
        "SNPS","CDNS","ROP","FTNT","CTAS","CSX","HCA","MSI","ORLY","AZO","PCAR",
        "NUE","SHW","TEL","AFL","ALL","CMI","CARR","PWR","PAYX","EW","FAST","FICO",
        "WELL","DLR","O","VRSK","IDXX","A","GPN","KMB","PPG","MTD","AME","ROK",
        "WMB","OTIS","EFX","HSY","KEYS","ANSS","VICI","KR","IQV","EPAM","TROW",
        "STZ","DLTR","SBAC","HAL","LHX","BK","BAX","AKAM","WEC","XYL","CTSH",
        "BALL","AVB","ALGN","TTWO","TSN","DVN","HES","MPC","VLO","PSX","EOG",
        "CCI","PEG","ES","ED","D","FE","EXC","AEP","PCG","XEL","AWK","ETR",
        "TMUS","VZ","CMCSA","CHTR","DIS","PARA","WBD","OMC","IPG","NWS","FOX",
        "NWSA","LVS","MGM","WYNN","MAR","HLT","CCL","RCL","NCLH","UAL","DAL",
        "AAL","LUV","ALK","JBLU","FLT","EXPE","BOOKING","ABNB","UBER","LYFT",
    ]


def _nasdaq100() -> List[str]:
    """Pull NASDAQ 100 from iShares QQQ-equivalent ETF holdings."""
    try:
        import pandas as pd
        from io import StringIO
        # iShares NASDAQ 100 ETF (CNDX/IVQ) — using QQQ Invesco public data
        url = ("https://www.ishares.com/us/products/239607/ishares-nasdaq-100-etf/"
               "1467271812596.ajax?fileType=csv&fileName=CNDX_holdings&dataType=fund")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            start = next((i for i, l in enumerate(lines) if "Ticker" in l or "ticker" in l), 0)
            df = pd.read_csv(StringIO("\n".join(lines[start:])))
            col = next((c for c in df.columns if "ticker" in c.lower()), None)
            if col:
                tickers = [t for t in df[col].dropna().tolist()
                           if isinstance(t, str) and t.isalpha() and len(t) <= 5]
                if len(tickers) > 50:
                    log.info("NASDAQ 100: fetched %d tickers from iShares", len(tickers))
                    return tickers
    except Exception as e:
        log.warning("NASDAQ 100 iShares fetch failed: %s — using fallback", e)

    # Fallback: hardcoded NASDAQ 100 core tickers
    return [
        "MSFT","AAPL","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AVGO","COST",
        "NFLX","AMD","PEP","CSCO","ADBE","TMUS","TXN","QCOM","INTU","AMGN",
        "HON","AMAT","BKNG","ISRG","VRTX","PANW","ADP","REGN","LRCX","MU",
        "GILD","ADI","MELI","KLAC","SNPS","CDNS","MDLZ","PYPL","CTAS","ORLY",
        "MRVL","CEG","FTNT","NXPI","CHTR","ABNB","PAYX","MNST","ROP","WDAY",
        "PCAR","ROST","KDP","ODFL","FAST","DXCM","TEAM","IDXX","VRSK","CPRT",
        "GEHC","EXC","ON","CTSH","BIIB","TTWO","ZS","SIRI","DLTR","WBD","ILMN",
        "ENPH","ALGN","JD","LCID","PDD","ASML","CRWD","DDOG","SNOW","NET","MDB",
        "ZM","OKTA","DOCU","COIN","HOOD","RBLX","PLTR","PATH","U","AFRM",
        "RIVN","LYFT","GRAB","SOFI","OPEN","WISH","FROG","GTLB","CFLT","HUBS",
    ]


def _russell1000() -> List[str]:
    try:
        import pandas as pd
        from io import StringIO
        url = ("https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/"
               "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            start = next((i for i, l in enumerate(lines) if "Ticker" in l or "ticker" in l), 0)
            df = pd.read_csv(StringIO("\n".join(lines[start:])))
            col = next((c for c in df.columns if "ticker" in c.lower()), None)
            if col:
                return [t for t in df[col].dropna().tolist()
                        if isinstance(t, str) and t.isalpha() and len(t) <= 5]
    except Exception as e:
        log.warning("Russell 1000 iShares fetch failed: %s — using fallback", e)

    # Fallback: known Russell 1000 mid-caps not typically in S&P 500
    return [
        "ALLE","AOS","AWK","BRO","CBOE","CDW","CHRW","CINF","CLH","COHR",
        "CSGP","DPZ","DT","EG","EME","ENTG","EWBC","EXAS","FBIN","FLS",
        "GATX","GGG","GMED","GPC","HALO","HLNE","IBKR","IEX","INSP","ITT",
        "KBR","KNSL","LII","LNTH","LOGI","LOPE","LRN","LSTR","MAN","MATX",
        "MEDP","MKSI","MMSI","MSA","MSM","MTZ","NXST","OGE","OLED","ORI",
        "OTEX","PCTY","PEN","PFSI","PIPR","PLMR","PODD","POOL","PRGS","PSN",
        "PTC","RLI","RMBS","RNR","ROAD","RRX","RYAN","SAIA","SCI","SITE",
        "SLGN","SM","SMAR","SNX","SSD","SSNC","STE","STRA","SWX","TNL",
        "TRMK","TRNO","TTC","TXRH","UFP","UFPI","UNUM","VLY","VSH","WBS",
        "WDFC","WMS","WNS","WSFS","WSO","WTS","XPEL","ZWS","ACLS","ACIW",
        "ADTN","ALKT","AMPH","AMSF","ANIP","AOSL","ARCB","ARCC","ARLO",
        "AROW","ARTNA","ASIX","ASND","ATRI","ATSG","AVNT","BANF","BANR",
        "BCPC","BFIN","BLKB","BMI","BPOP","BRKL","BUSE","CABO","CATO",
        "CBSH","CCMP","CCRN","CENT","CENTA","CENX","CEVA","CFFI","CFFN",
        "CHCO","CHDN","CHEF","CHMG","CLFD","CLNE","CMCO","CNOB","CNXN",
        "CODA","COKE","COLB","COLL","CONN","COOP","CORE","CORT","TOWN",
        "NAVI","PFBC","PEBO","PNFP","PPBI","PRAA","PRIM","PUMP","QCRH",
        "QDEL","RDUS","REL","RICK","RMNI","ROCK","ROIC","RUSHA","SASR",
        "SBCF","SBSI","SCHL","SCSC","SFBS","SFNC","SHEN","SHOO","SIBN",
        "SKYW","SLCA","SMBC","SMPL","SNEX","SPTN","SPXC","SRCE","STBA",
        "STFC","STNG","STRA","SUPN","TCBK","TBNK","TGNA","TIPT","TOWN",
        "TRST","TRUP","TSBK","UBSI","UCBI","UCTT","UFCS","ULTA","UMBF",
        "UMPQ","UNF","UNFI","UNIT","UONE","UPBD","URBN","UVSP","VBTX",
        "VCEL","VCNX","VECO","VIAV","VIEW","VIRT","VIVO","VLGEA","VRTS",
        "VSCO","VSEC","VTOL","WABC","WAFD","WASH","WERN","WEYS","WINA",
        "WLFC","WMGI","WRLD","WSBC","WTFC","WTTR","WWW","XNCR","YORW",
    ]


def _yfinance_screeners() -> List[str]:
    results = []
    try:
        import yfinance as yf
        for screen in ["most_actives", "day_gainers",
                       "growth_technology_stocks", "undervalued_growth_stocks"]:
            try:
                data = yf.screen(screen, count=50)
                if data and "quotes" in data:
                    for q in data["quotes"]:
                        sym = q.get("symbol", "")
                        if sym and "." not in sym and len(sym) <= 5:
                            results.append(sym)
            except Exception:
                pass
    except Exception as e:
        log.warning("yfinance screener failed: %s", e)
    return results

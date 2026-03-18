"""
data_fetcher.py
───────────────
Pulls market data, fundamentals, insider activity, and technical
indicators for a given ticker using yfinance (free, no key required)
with optional Alpha Vantage enrichment.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class TechnicalSignals:
    price: float = 0.0
    price_52w_high: float = 0.0
    price_52w_low: float = 0.0
    pct_from_52w_high: float = 0.0
    pct_from_52w_low: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    rsi_14: float = 50.0
    volume_ratio: float = 1.0      # current / 20d avg
    macd_signal: str = "neutral"   # bullish / bearish / neutral
    trend_direction: str = "neutral"  # uptrend / downtrend / sideways
    above_200ema: bool = False
    golden_cross: bool = False     # 50 > 200 EMA
    at_support: bool = False
    volume_accumulation: bool = False
    risk_reward_ratio: float = 1.0  # estimated upside / downside


@dataclass
class Fundamentals:
    market_cap: float = 0.0
    pe_ratio: float = 0.0
    forward_pe: float = 0.0
    peg_ratio: float = 0.0
    price_to_sales: float = 0.0
    price_to_book: float = 0.0
    revenue_growth_yoy: float = 0.0
    earnings_growth_yoy: float = 0.0
    gross_margin: float = 0.0
    operating_margin: float = 0.0
    free_cash_flow: float = 0.0
    cash_on_hand: float = 0.0
    total_debt: float = 0.0
    debt_to_equity: float = 0.0
    return_on_equity: float = 0.0
    current_ratio: float = 0.0
    analyst_target_price: float = 0.0
    analyst_rating: str = "N/A"
    sector: str = "Unknown"
    industry: str = "Unknown"
    description: str = ""
    employees: int = 0


@dataclass
class InsiderActivity:
    net_buying_last_6m: float = 0.0   # USD, positive = net buy
    insider_ownership_pct: float = 0.0
    institutional_ownership_pct: float = 0.0
    recent_buys: int = 0
    recent_sells: int = 0
    bullish: bool = False


@dataclass
class StockData:
    ticker: str
    name: str = ""
    technicals: TechnicalSignals = field(default_factory=TechnicalSignals)
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    insider: InsiderActivity = field(default_factory=InsiderActivity)
    avg_daily_volume: float = 0.0
    error: Optional[str] = None


# ─── Core Fetcher ─────────────────────────────────────────────────────────────

class DataFetcher:
    def __init__(self, config: dict):
        self.cfg = config
        self.tech_cfg = config.get("technicals", {})
        self._cache: Dict[str, StockData] = {}

    def fetch(self, ticker: str) -> StockData:
        if ticker in self._cache:
            return self._cache[ticker]

        sd = StockData(ticker=ticker)
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            sd.name = info.get("longName") or info.get("shortName", ticker)
            sd.fundamentals = self._parse_fundamentals(info)
            sd.avg_daily_volume = float(info.get("averageVolume", 0) or 0)

            hist_1y = t.history(period="1y", interval="1d")
            hist_3m = t.history(period="3mo", interval="1d")

            if hist_1y.empty:
                sd.error = "No price history"
                return sd

            sd.technicals = self._calc_technicals(hist_1y, hist_3m)
            sd.insider = self._parse_insider(t, info)

        except Exception as exc:
            sd.error = str(exc)
            log.warning("[%s] Fetch error: %s", ticker, exc)

        self._cache[ticker] = sd
        return sd

    # ── Technicals ────────────────────────────────────────────────────────────

    def _calc_technicals(self, hist_1y: pd.DataFrame, hist_3m: pd.DataFrame) -> TechnicalSignals:
        sig = TechnicalSignals()
        close = hist_1y["Close"].astype(float)
        volume = hist_1y["Volume"].astype(float)

        sig.price = float(close.iloc[-1])
        sig.price_52w_high = float(close.max())
        sig.price_52w_low = float(close.min())
        sig.pct_from_52w_high = (sig.price - sig.price_52w_high) / sig.price_52w_high
        sig.pct_from_52w_low = (sig.price - sig.price_52w_low) / sig.price_52w_low

        # EMAs
        sig.ema_20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        sig.ema_50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        sig.ema_200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        sig.above_200ema = sig.price > sig.ema_200
        sig.golden_cross = sig.ema_50 > sig.ema_200

        # RSI
        sig.rsi_14 = float(self._rsi(close, 14))

        # Volume ratio
        avg_vol_20 = float(volume.tail(20).mean())
        recent_vol = float(volume.tail(5).mean())
        sig.volume_ratio = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
        sig.volume_accumulation = sig.volume_ratio > self.tech_cfg.get("volume_surge_ratio", 1.5)

        # MACD
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        signal_line = macd.ewm(span=9, adjust=False).mean()
        if macd.iloc[-1] > signal_line.iloc[-1] and macd.iloc[-2] <= signal_line.iloc[-2]:
            sig.macd_signal = "bullish_cross"
        elif macd.iloc[-1] > signal_line.iloc[-1]:
            sig.macd_signal = "bullish"
        elif macd.iloc[-1] < signal_line.iloc[-1]:
            sig.macd_signal = "bearish"
        else:
            sig.macd_signal = "neutral"

        # Trend direction (slope of 50 EMA over last 20 days)
        ema50_series = close.ewm(span=50, adjust=False).mean()
        slope = float(ema50_series.iloc[-1]) - float(ema50_series.iloc[-20])
        if slope > 0 and sig.above_200ema:
            sig.trend_direction = "uptrend"
        elif slope < 0 and not sig.above_200ema:
            sig.trend_direction = "downtrend"
        else:
            sig.trend_direction = "sideways"

        # At support: price within 3% of 200 EMA or 52w low bounce zone
        support_zone = min(sig.ema_200, sig.price_52w_low * 1.15)
        sig.at_support = sig.price <= support_zone * 1.03

        # Risk/reward: upside to 52w high vs downside to 200 EMA
        upside = (sig.price_52w_high - sig.price) / sig.price if sig.price > 0 else 0
        downside = max((sig.price - sig.ema_200) / sig.price, 0.05)
        sig.risk_reward_ratio = upside / downside if downside > 0 else 1.0

        return sig

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not rsi.empty else 50.0

    # ── Fundamentals ──────────────────────────────────────────────────────────

    def _parse_fundamentals(self, info: dict) -> Fundamentals:
        def safe(key, default=0.0):
            val = info.get(key)
            return float(val) if val is not None else default

        f = Fundamentals()
        f.market_cap = safe("marketCap")
        f.pe_ratio = safe("trailingPE")
        f.forward_pe = safe("forwardPE")
        f.peg_ratio = safe("pegRatio")
        f.price_to_sales = safe("priceToSalesTrailing12Months")
        f.price_to_book = safe("priceToBook")
        f.revenue_growth_yoy = safe("revenueGrowth")
        f.earnings_growth_yoy = safe("earningsGrowth")
        f.gross_margin = safe("grossMargins")
        f.operating_margin = safe("operatingMargins")
        f.free_cash_flow = safe("freeCashflow")
        f.cash_on_hand = safe("totalCash")
        f.total_debt = safe("totalDebt")
        f.debt_to_equity = safe("debtToEquity") / 100.0 if info.get("debtToEquity") else 0.0
        f.return_on_equity = safe("returnOnEquity")
        f.current_ratio = safe("currentRatio")
        f.analyst_target_price = safe("targetMeanPrice")
        f.analyst_rating = info.get("recommendationKey", "N/A")
        f.sector = info.get("sector", "Unknown")
        f.industry = info.get("industry", "Unknown")
        f.description = (info.get("longBusinessSummary") or "")[:600]
        f.employees = int(info.get("fullTimeEmployees") or 0)
        return f

    # ── Insider ───────────────────────────────────────────────────────────────

    def _parse_insider(self, ticker_obj: yf.Ticker, info: dict) -> InsiderActivity:
        ia = InsiderActivity()
        ia.insider_ownership_pct = float(info.get("heldPercentInsiders") or 0)
        ia.institutional_ownership_pct = float(info.get("heldPercentInstitutions") or 0)
        try:
            trades = ticker_obj.insider_purchases
            if trades is not None and not trades.empty:
                ia.recent_buys = len(trades[trades.get("Transaction", "").str.contains("Buy", na=False)])
        except Exception:
            pass
        try:
            sales = ticker_obj.insider_transactions
            if sales is not None and not sales.empty:
                buys = sales[sales.get("Shares", 0) > 0]
                sells = sales[sales.get("Shares", 0) < 0]
                ia.recent_buys = len(buys)
                ia.recent_sells = len(sells)
                ia.net_buying_last_6m = float(
                    (buys.get("Value", pd.Series(dtype=float)).sum() or 0)
                    - abs(sells.get("Value", pd.Series(dtype=float)).sum() or 0)
                )
        except Exception:
            pass
        ia.bullish = (ia.recent_buys > ia.recent_sells) or ia.net_buying_last_6m > 0
        return ia


# ─── Dynamic Universe Discovery ──────────────────────────────────────────────

def discover_momentum_tickers(count: int = 10) -> List[str]:
    """
    Pull high-momentum, high-volume tickers from Yahoo Finance screeners.
    Returns a deduplicated list of ticker strings.
    """
    candidates = []
    try:
        # High-growth tech names via yfinance screener proxy
        screens = [
            "most_actives",
            "day_gainers",
            "growth_technology_stocks",
        ]
        for screen in screens:
            try:
                data = yf.screen(screen, count=20)
                if data and "quotes" in data:
                    for q in data["quotes"]:
                        sym = q.get("symbol", "")
                        if sym and "." not in sym:
                            candidates.append(sym)
            except Exception:
                pass
        # Deduplicate, keep first N
        seen = set()
        result = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                result.append(c)
            if len(result) >= count:
                break
        return result
    except Exception as exc:
        log.warning("Dynamic discovery failed: %s", exc)
        return []

"""
macro_analyzer.py
─────────────────
Fetches and interprets current macro environment signals:
  - Fed rate cycle (10yr/2yr yield, spread)
  - VIX (fear/greed)
  - DXY (dollar strength)
  - Sector rotation signals
  - Earnings season calendar awareness
  - Economic surprise index proxy

Used by the scoring engine to apply macro headwind/tailwind adjustments
to each pick's composite score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class MacroContext:
    # Market regime
    market_regime: str = "unknown"      # bull / bear / sideways / volatile
    risk_on: bool = True                # risk-on vs risk-off environment

    # Rates
    rate_10yr: float = 0.0
    rate_2yr: float = 0.0
    yield_curve_spread: float = 0.0    # 10yr - 2yr (negative = inverted = recession warning)
    yield_curve_status: str = "normal"  # normal / flat / inverted

    # Volatility
    vix: float = 20.0
    vix_regime: str = "normal"         # calm / normal / elevated / fear / panic

    # Dollar
    dxy: float = 100.0
    dollar_trend: str = "neutral"      # strong / neutral / weak

    # Sector
    best_sectors: list = None
    avoid_sectors: list = None

    # Earnings
    earnings_season: bool = False
    days_to_next_earnings_season: int = 0

    # Overall macro score adjustment (-15 to +15 points)
    score_adjustment: float = 0.0
    regime_description: str = ""

    def __post_init__(self):
        if self.best_sectors is None:
            self.best_sectors = []
        if self.avoid_sectors is None:
            self.avoid_sectors = []


class MacroAnalyzer:
    def __init__(self, config: dict):
        self.cfg = config

    def get_context(self) -> MacroContext:
        ctx = MacroContext()
        try:
            import yfinance as yf

            # ── Yield Curve ───────────────────────────────────────────────────
            try:
                tnx = yf.Ticker("^TNX").history(period="5d")
                tyx = yf.Ticker("^TYX").history(period="5d")
                irx = yf.Ticker("^IRX").history(period="5d")

                if not tnx.empty:
                    ctx.rate_10yr = float(tnx["Close"].iloc[-1]) / 10
                if not irx.empty:
                    ctx.rate_2yr = float(irx["Close"].iloc[-1]) / 10
                ctx.yield_curve_spread = ctx.rate_10yr - ctx.rate_2yr
                if ctx.yield_curve_spread < -0.5:
                    ctx.yield_curve_status = "inverted"
                elif ctx.yield_curve_spread < 0.2:
                    ctx.yield_curve_status = "flat"
                else:
                    ctx.yield_curve_status = "normal"
            except Exception as e:
                log.debug("Yield curve fetch error: %s", e)

            # ── VIX ───────────────────────────────────────────────────────────
            try:
                vix_data = yf.Ticker("^VIX").history(period="5d")
                if not vix_data.empty:
                    ctx.vix = float(vix_data["Close"].iloc[-1])
                if ctx.vix < 15:
                    ctx.vix_regime = "calm"
                elif ctx.vix < 20:
                    ctx.vix_regime = "normal"
                elif ctx.vix < 30:
                    ctx.vix_regime = "elevated"
                elif ctx.vix < 40:
                    ctx.vix_regime = "fear"
                else:
                    ctx.vix_regime = "panic"
            except Exception as e:
                log.debug("VIX fetch error: %s", e)

            # ── DXY (Dollar) ─────────────────────────────────────────────────
            try:
                dxy_data = yf.Ticker("DX-Y.NYB").history(period="30d")
                if not dxy_data.empty:
                    ctx.dxy = float(dxy_data["Close"].iloc[-1])
                    dxy_1m_ago = float(dxy_data["Close"].iloc[0])
                    dxy_change = (ctx.dxy - dxy_1m_ago) / dxy_1m_ago
                    if dxy_change > 0.02:
                        ctx.dollar_trend = "strong"
                    elif dxy_change < -0.02:
                        ctx.dollar_trend = "weak"
                    else:
                        ctx.dollar_trend = "neutral"
            except Exception as e:
                log.debug("DXY fetch error: %s", e)

            # ── SPY trend for regime ──────────────────────────────────────────
            try:
                spy = yf.Ticker("SPY").history(period="1y")
                if not spy.empty:
                    price = float(spy["Close"].iloc[-1])
                    ema_200 = float(spy["Close"].ewm(span=200, adjust=False).mean().iloc[-1])
                    spy_1m = float(spy["Close"].iloc[-21]) if len(spy) > 21 else price
                    spy_3m = float(spy["Close"].iloc[-63]) if len(spy) > 63 else price

                    if price > ema_200 and (price / spy_3m - 1) > 0.05:
                        ctx.market_regime = "bull"
                        ctx.risk_on = True
                    elif price < ema_200 and (price / spy_3m - 1) < -0.05:
                        ctx.market_regime = "bear"
                        ctx.risk_on = False
                    elif ctx.vix > 25:
                        ctx.market_regime = "volatile"
                        ctx.risk_on = False
                    else:
                        ctx.market_regime = "sideways"
                        ctx.risk_on = True
            except Exception as e:
                log.debug("SPY regime fetch error: %s", e)

        except ImportError:
            log.warning("yfinance not installed — using default macro context")

        # ── Sector Rotation Logic ─────────────────────────────────────────────
        ctx.best_sectors, ctx.avoid_sectors = self._sector_rotation(ctx)

        # ── Earnings Season ───────────────────────────────────────────────────
        ctx.earnings_season, ctx.days_to_next_earnings_season = self._earnings_season_check()

        # ── Score Adjustment ─────────────────────────────────────────────────
        ctx.score_adjustment = self._calc_adjustment(ctx)
        ctx.regime_description = self._describe_regime(ctx)

        log.info(
            "Macro: regime=%s vix=%.1f yield_spread=%.2f dxy=%.1f adjustment=%+.1f",
            ctx.market_regime, ctx.vix, ctx.yield_curve_spread, ctx.dxy, ctx.score_adjustment,
        )
        return ctx

    def _sector_rotation(self, ctx: MacroContext) -> tuple:
        if ctx.market_regime == "bull" and ctx.rate_10yr < 5.0:
            return (
                ["Technology", "Consumer Discretionary", "Communication Services"],
                ["Utilities", "Real Estate"],
            )
        elif ctx.market_regime == "bull" and ctx.rate_10yr >= 5.0:
            return (
                ["Energy", "Financials", "Healthcare"],
                ["Real Estate", "Consumer Discretionary"],
            )
        elif ctx.market_regime == "bear":
            return (
                ["Healthcare", "Consumer Staples", "Utilities"],
                ["Technology", "Consumer Discretionary", "Real Estate"],
            )
        elif ctx.vix > 30:  # volatile
            return (
                ["Healthcare", "Consumer Staples"],
                ["Technology", "Real Estate", "Consumer Discretionary"],
            )
        else:
            return (
                ["Technology", "Healthcare", "Financials"],
                ["Real Estate"],
            )

    def _earnings_season_check(self) -> tuple:
        """
        Earnings seasons: Jan, Apr, Jul, Oct (roughly weeks 2-6 of those months).
        Returns (in_season: bool, days_to_next: int)
        """
        today = date.today()
        season_months = [1, 4, 7, 10]
        in_season = today.month in season_months and 7 <= today.day <= 42
        # Calculate days to next season start
        next_season_dates = []
        for year in [today.year, today.year + 1]:
            for m in season_months:
                nd = date(year, m, 7)
                if nd > today:
                    next_season_dates.append(nd)
        next_season = min(next_season_dates) if next_season_dates else date(today.year + 1, 1, 7)
        days_to_next = (next_season - today).days
        return in_season, days_to_next

    def _calc_adjustment(self, ctx: MacroContext) -> float:
        adj = 0.0
        # VIX penalty
        if ctx.vix_regime == "panic":
            adj -= 10
        elif ctx.vix_regime == "fear":
            adj -= 5
        elif ctx.vix_regime == "calm":
            adj += 3

        # Regime bonus
        if ctx.market_regime == "bull":
            adj += 5
        elif ctx.market_regime == "bear":
            adj -= 8

        # Yield curve
        if ctx.yield_curve_status == "inverted":
            adj -= 5
        elif ctx.yield_curve_status == "normal":
            adj += 2

        # Dollar (strong dollar hurts US multinationals)
        if ctx.dollar_trend == "strong":
            adj -= 3

        return max(-15, min(15, adj))

    def _describe_regime(self, ctx: MacroContext) -> str:
        parts = []
        if ctx.market_regime == "bull":
            parts.append("📈 Bull market — risk assets favored")
        elif ctx.market_regime == "bear":
            parts.append("📉 Bear market — defensive positioning preferred")
        elif ctx.market_regime == "volatile":
            parts.append("⚡ High volatility — be selective, wait for confirmation")
        else:
            parts.append("↔️ Sideways market — stock-picking alpha matters most")

        if ctx.yield_curve_status == "inverted":
            parts.append("⚠️ Inverted yield curve — recession signal active")
        if ctx.vix > 25:
            parts.append(f"😰 VIX {ctx.vix:.0f} — elevated fear, dips may be buying ops")
        if ctx.earnings_season:
            parts.append("📊 Earnings season — volatility expected around reports")

        return " | ".join(parts)

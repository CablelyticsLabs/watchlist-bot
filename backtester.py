"""
backtester.py
─────────────
Simulates the bot running on historical data to measure how well the
scoring framework would have performed.

Usage:
    python backtester.py --tickers NVDA MSFT GOOGL --start 2022-01-01 --end 2024-12-31
    python backtester.py --preset mega_cap --start 2023-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys
sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger("Backtester")

# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class BacktestPick:
    date: str
    ticker: str
    score: float
    buy_price: float
    price_30d: float = 0.0
    price_90d: float = 0.0
    price_365d: float = 0.0
    return_30d: float = 0.0
    return_90d: float = 0.0
    return_365d: float = 0.0


@dataclass
class BacktestResult:
    period: str
    total_picks: int
    avg_score: float
    win_rate_30d: float
    win_rate_90d: float
    win_rate_365d: float
    avg_return_30d: float
    avg_return_90d: float
    avg_return_365d: float
    vs_spy_30d: float       # alpha vs S&P 500
    vs_spy_90d: float
    vs_spy_365d: float
    top_picks: List[BacktestPick] = field(default_factory=list)
    worst_picks: List[BacktestPick] = field(default_factory=list)


PRESET_UNIVERSES = {
    "mega_cap": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ORCL", "CRM"],
    "ai_theme": ["NVDA", "AMD", "PLTR", "CRWD", "NET", "DDOG", "SNOW", "MDB", "AI", "SMCI"],
    "growth": ["NVDA", "MSFT", "META", "GOOGL", "AMZN", "TSLA", "PLTR", "RKLB", "ASTS", "ARM"],
    "diversified": ["NVDA", "MSFT", "GOOGL", "JPM", "UNH", "LLY", "V", "AVGO", "AMZN", "META"],
}


class Backtester:
    def __init__(self, config_path: str = "config.yaml"):
        import yaml
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

    def run(
        self,
        tickers: List[str],
        start_date: str,
        end_date: Optional[str] = None,
        top_n: int = 5,
    ) -> BacktestResult:
        import yfinance as yf
        from src.framework_loader import load_framework
        from src.data_fetcher import DataFetcher
        from src.sentiment_analyzer import SentimentAnalyzer, SentimentScore
        from src.scoring_engine import ScoringEngine

        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
        start = datetime.strptime(start_date, "%Y-%m-%d")

        framework = load_framework(self.cfg.get("framework", {}).get("url", ""))
        fetcher = DataFetcher(self.cfg)
        scorer = ScoringEngine(self.cfg, framework)

        # Generate monthly rebalance dates
        rebalance_dates = []
        current = start
        while current < end:
            rebalance_dates.append(current)
            # monthly
            next_month = current.replace(day=1) + timedelta(days=32)
            current = next_month.replace(day=1)

        all_picks: List[BacktestPick] = []

        # Get SPY as benchmark
        spy_hist = yf.download("SPY", start=start_date, end=end.strftime("%Y-%m-%d"), progress=False)

        for rb_date in rebalance_dates:
            log.info("Backtesting rebalance: %s", rb_date.strftime("%Y-%m-%d"))
            date_str = rb_date.strftime("%Y-%m-%d")

            scored = []
            for ticker in tickers:
                try:
                    # Fetch data as-of the rebalance date
                    t = yf.Ticker(ticker)
                    hist = t.history(start=(rb_date - timedelta(days=365)).strftime("%Y-%m-%d"),
                                     end=date_str)
                    if hist.empty:
                        continue

                    sd = fetcher.fetch(ticker)
                    # Override price to historical
                    hist_price = float(hist["Close"].iloc[-1])
                    sd.technicals.price = hist_price

                    sent = SentimentScore(ticker=ticker)
                    result = scorer.score(sd, sent)
                    result.price = hist_price
                    scored.append(result)
                except Exception as exc:
                    log.debug("[%s] Backtest error: %s", ticker, exc)

            scored.sort(key=lambda x: x.composite_score, reverse=True)
            picks_today = scored[:top_n]

            for pick in picks_today:
                # Measure forward returns
                try:
                    t = yf.Ticker(pick.ticker)
                    fwd = t.history(
                        start=date_str,
                        end=(rb_date + timedelta(days=400)).strftime("%Y-%m-%d")
                    )
                    if fwd.empty:
                        continue

                    bp = BacktestPick(
                        date=date_str,
                        ticker=pick.ticker,
                        score=pick.composite_score,
                        buy_price=float(fwd["Close"].iloc[0]),
                    )

                    def fwd_price(days: int) -> float:
                        idx = min(days, len(fwd) - 1)
                        return float(fwd["Close"].iloc[idx])

                    bp.price_30d = fwd_price(21)
                    bp.price_90d = fwd_price(63)
                    bp.price_365d = fwd_price(252)
                    bp.return_30d = (bp.price_30d - bp.buy_price) / bp.buy_price
                    bp.return_90d = (bp.price_90d - bp.buy_price) / bp.buy_price
                    bp.return_365d = (bp.price_365d - bp.buy_price) / bp.buy_price
                    all_picks.append(bp)
                except Exception:
                    pass

        # SPY returns over same period
        spy_return = 0.0
        if not spy_hist.empty:
            spy_start = float(spy_hist["Close"].iloc[0])
            spy_end = float(spy_hist["Close"].iloc[-1])
            spy_return = (spy_end - spy_start) / spy_start

        # Aggregate
        if not all_picks:
            return BacktestResult(
                period=f"{start_date} to {end.strftime('%Y-%m-%d')}",
                total_picks=0, avg_score=0, win_rate_30d=0, win_rate_90d=0,
                win_rate_365d=0, avg_return_30d=0, avg_return_90d=0,
                avg_return_365d=0, vs_spy_30d=0, vs_spy_90d=0, vs_spy_365d=0,
            )

        def avg(lst): return sum(lst) / len(lst) if lst else 0
        def wr(lst): return sum(1 for x in lst if x > 0) / len(lst) if lst else 0

        r30 = [p.return_30d for p in all_picks]
        r90 = [p.return_90d for p in all_picks]
        r365 = [p.return_365d for p in all_picks]

        result = BacktestResult(
            period=f"{start_date} to {end.strftime('%Y-%m-%d')}",
            total_picks=len(all_picks),
            avg_score=avg([p.score for p in all_picks]),
            win_rate_30d=wr(r30),
            win_rate_90d=wr(r90),
            win_rate_365d=wr(r365),
            avg_return_30d=avg(r30),
            avg_return_90d=avg(r90),
            avg_return_365d=avg(r365),
            vs_spy_30d=avg(r30) - (spy_return / 12),
            vs_spy_90d=avg(r90) - (spy_return / 4),
            vs_spy_365d=avg(r365) - spy_return,
        )

        sorted_picks = sorted(all_picks, key=lambda x: x.return_365d, reverse=True)
        result.top_picks = sorted_picks[:5]
        result.worst_picks = sorted_picks[-5:]

        self._print_report(result)
        self._save_report(result)
        return result

    def _print_report(self, r: BacktestResult):
        print(f"\n{'═'*60}")
        print(f"  📊  BACKTEST RESULTS — {r.period}")
        print(f"{'═'*60}")
        print(f"  Total picks evaluated : {r.total_picks}")
        print(f"  Avg framework score   : {r.avg_score:.1f}/100")
        print(f"\n  {'Period':<12} {'Win Rate':>10} {'Avg Return':>12} {'vs SPY (alpha)':>16}")
        print(f"  {'─'*52}")
        print(f"  {'30 days':<12} {r.win_rate_30d:>9.1%}  {r.avg_return_30d:>+11.1%}  {r.vs_spy_30d:>+14.1%}")
        print(f"  {'90 days':<12} {r.win_rate_90d:>9.1%}  {r.avg_return_90d:>+11.1%}  {r.vs_spy_90d:>+14.1%}")
        print(f"  {'365 days':<12} {r.win_rate_365d:>9.1%}  {r.avg_return_365d:>+11.1%}  {r.vs_spy_365d:>+14.1%}")
        if r.top_picks:
            print(f"\n  🏆 Best Picks:")
            for p in r.top_picks:
                print(f"     {p.date} {p.ticker:<6} score={p.score:.0f}  +{p.return_365d:.1%} (1yr)")
        if r.worst_picks:
            print(f"\n  💀 Worst Picks:")
            for p in r.worst_picks:
                print(f"     {p.date} {p.ticker:<6} score={p.score:.0f}  {p.return_365d:.1%} (1yr)")
        print(f"{'═'*60}\n")

    def _save_report(self, r: BacktestResult):
        out = Path("output")
        out.mkdir(exist_ok=True)
        fname = out / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        data = {
            "period": r.period,
            "total_picks": r.total_picks,
            "avg_score": round(r.avg_score, 2),
            "win_rates": {"30d": r.win_rate_30d, "90d": r.win_rate_90d, "365d": r.win_rate_365d},
            "avg_returns": {"30d": r.avg_return_30d, "90d": r.avg_return_90d, "365d": r.avg_return_365d},
            "alpha_vs_spy": {"30d": r.vs_spy_30d, "90d": r.vs_spy_90d, "365d": r.vs_spy_365d},
        }
        fname.write_text(json.dumps(data, indent=2))
        log.info("Backtest saved to %s", fname)


def main():
    parser = argparse.ArgumentParser(description="WatchlistBot Backtester")
    parser.add_argument("--tickers", nargs="+", help="Ticker symbols to test")
    parser.add_argument("--preset", choices=list(PRESET_UNIVERSES.keys()), help="Use a preset universe")
    parser.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--top-n", type=int, default=5, help="Top N picks per rebalance")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    tickers = args.tickers or PRESET_UNIVERSES.get(args.preset, PRESET_UNIVERSES["growth"])
    bt = Backtester(args.config)
    bt.run(tickers=tickers, start_date=args.start, end_date=args.end, top_n=args.top_n)


if __name__ == "__main__":
    main()

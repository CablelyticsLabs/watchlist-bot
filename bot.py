"""
bot.py
──────
Main orchestration loop for the WatchlistBot.

Workflow:
  1. Load config
  2. Fetch framework from configured URL
  3. Build candidate universe (static watchlist + dynamic discovery)
  4. Run data + sentiment fetch in parallel
  5. Score every ticker against the framework
  6. Rank and filter to top N
  7. Post to Discord at configured time (or immediately if --now flag used)
  8. Save JSON + Markdown output
  9. Sleep until next scheduled run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pytz
import schedule
import yaml

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
from src.framework_loader import load_framework
from src.data_fetcher import DataFetcher, discover_momentum_tickers
from src.sentiment_analyzer import SentimentAnalyzer
from src.scoring_engine import ScoringEngine, ScoredStock
from src.discord_poster import DiscordPoster
from src.macro_analyzer import MacroAnalyzer, MacroContext

# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"),
        ],
    )

log = logging.getLogger("WatchlistBot")


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    log.info("Config loaded from %s", path)
    return cfg


# ─── Core Analysis Run ────────────────────────────────────────────────────────

def run_analysis(cfg: dict) -> List[ScoredStock]:
    log.info("═══════════════════════════════════════════")
    log.info("  WatchlistBot Analysis Run Starting")
    log.info("═══════════════════════════════════════════")

    # 1. Load framework
    framework_url = cfg.get("framework", {}).get("url", "")
    framework = load_framework(framework_url)
    log.info("Framework loaded: %d questions", len(framework))

    # 2. Build universe
    universe_cfg = cfg.get("universe", {})
    tickers = list(universe_cfg.get("watchlist", []))

    if universe_cfg.get("auto_discover", False):
        discovered = discover_momentum_tickers(universe_cfg.get("auto_discover_count", 10))
        new_tickers = [t for t in discovered if t not in tickers]
        log.info("Discovered %d new tickers: %s", len(new_tickers), new_tickers)
        tickers.extend(new_tickers)

    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order
    log.info("Analyzing %d tickers: %s", len(tickers), tickers)

    # 3. Macro context (runs once per session)
    log.info("Fetching macro environment context...")
    macro = MacroAnalyzer(cfg).get_context()
    log.info("Macro regime: %s | Adjustment: %+.1f pts", macro.market_regime, macro.score_adjustment)
    log.info("Best sectors: %s", macro.best_sectors)
    log.info("Regime: %s", macro.regime_description)

    # Initialize engines
    fetcher = DataFetcher(cfg)
    sentiment_analyzer = SentimentAnalyzer(cfg)
    scorer = ScoringEngine(cfg, framework)
    fundamental_filters = cfg.get("fundamentals", {})
    min_vol = universe_cfg.get("min_avg_volume", 0)
    min_cap = universe_cfg.get("min_market_cap", 0)

    # 4. Parallel data fetch
    stock_data = {}
    log.info("Fetching market data...")
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetcher.fetch, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                sd = fut.result()
                stock_data[ticker] = sd
                log.debug("[%s] Fetched: $%.2f, cap $%.1fB", ticker, sd.technicals.price, sd.fundamentals.market_cap / 1e9)
            except Exception as exc:
                log.warning("[%s] Fetch error: %s", ticker, exc)

    # 5. Parallel sentiment fetch
    sentiment_data = {}
    log.info("Fetching sentiment data...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(sentiment_analyzer.analyze, t, stock_data.get(t, None) and stock_data[t].name or t): t
            for t in tickers if t in stock_data
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                ss = fut.result()
                sentiment_data[ticker] = ss
            except Exception as exc:
                log.warning("[%s] Sentiment error: %s", ticker, exc)

    # 6. Score and filter
    scored = []
    for ticker in tickers:
        sd = stock_data.get(ticker)
        if not sd or sd.error:
            log.debug("[%s] Skipping (no data / error: %s)", ticker, sd.error if sd else "None")
            continue

        # Volume filter
        if sd.avg_daily_volume < min_vol:
            log.debug("[%s] Skipping: avg volume %.0f < minimum %.0f", ticker, sd.avg_daily_volume, min_vol)
            continue

        # Market cap filter
        if sd.fundamentals.market_cap < min_cap:
            log.debug("[%s] Skipping: market cap $%.0fM < minimum", ticker, sd.fundamentals.market_cap / 1e6)
            continue

        # Fundamental filters
        f = sd.fundamentals
        if f.revenue_growth_yoy < fundamental_filters.get("min_revenue_growth_yoy", 0.0):
            log.debug("[%s] Skipping: revenue growth %.1%% below minimum", ticker, f.revenue_growth_yoy)
            continue

        sent = sentiment_data.get(ticker)
        from src.sentiment_analyzer import SentimentScore
        if sent is None:
            sent = SentimentScore(ticker=ticker)

        result = scorer.score(sd, sent)
        # Apply macro tailwind/headwind adjustment
        sector_bonus = 3.0 if f.sector in macro.best_sectors else (-3.0 if f.sector in macro.avoid_sectors else 0.0)
        result.composite_score = max(0, min(100, result.composite_score + macro.score_adjustment + sector_bonus))
        result.investment_rating = __import__('src.scoring_engine', fromlist=['get_rating']).get_rating(result.composite_score)
        scored.append(result)
        log.info(
            "[%s] Score: %.0f/100 | %s | Tech: %s | Sentiment: %s",
            ticker, result.composite_score, result.investment_rating,
            result.technical_grade, result.sentiment_grade
        )

    # 7. Rank by composite score
    scored.sort(key=lambda x: x.composite_score, reverse=True)
    top_n = cfg.get("output", {}).get("top_n", 5)
    top_picks = scored[:top_n]

    log.info("Top %d picks: %s", top_n, [f"{p.ticker}({p.composite_score:.0f})" for p in top_picks])
    return top_picks, scored, macro


# ─── Output Saving ────────────────────────────────────────────────────────────

def save_output(picks: List[ScoredStock], cfg: dict, all_scored: List[ScoredStock] = None, macro=None):
    output_cfg = cfg.get("output", {})
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    def pick_to_dict(p):
        return {
            "ticker": p.ticker,
            "name": p.name,
            "composite_score": round(p.composite_score, 2),
            "investment_rating": p.investment_rating,
            "technical_grade": p.technical_grade,
            "sentiment_grade": p.sentiment_grade,
            "price": p.price,
            "sector": p.sector,
            "entry_zone": p.entry_zone,
            "target_1y": p.target_1y,
            "target_3y": p.target_3y,
            "rationale_bullets": p.rationale_bullets,
            "risks": p.risks,
            "category_scores": [
                {"category": c.category, "score": round(c.score, 3)}
                for c in p.category_scores
            ],
        }

    macro_dict = {}
    if macro:
        macro_dict = {
            "yield10": round(macro.rate_10yr, 2),
            "yieldCurve": f"{macro.yield_curve_status.title()} ({macro.yield_curve_spread:+.2f})",
            "vix": round(macro.vix, 1),
            "vixRegime": macro.vix_regime.title(),
            "dxy": round(macro.dxy, 1),
            "dxyTrend": macro.dollar_trend.title(),
            "regime": macro.regime_description,
        }

    full_output = {
        "run_date": date_str,
        "generated_at": datetime.now().isoformat(),
        "picks": [pick_to_dict(p) for p in picks],
        "all_scores": [
            {"ticker": p.ticker, "composite_score": round(p.composite_score, 2)}
            for p in (all_scored or picks)
        ],
        "macro": macro_dict,
        "meta": {
            "topScore": round(picks[0].composite_score, 0) if picks else 0,
            "topTicker": picks[0].ticker if picks else "—",
            "avgScore": round(sum(p.composite_score for p in picks) / len(picks), 0) if picks else 0,
            "tickers": len(all_scored or picks),
            "regime": macro.market_regime.upper() if macro else "—",
            "vix": str(round(macro.vix, 1)) if macro else "—",
            "runDate": date_str,
        }
    }

    # Write dated file
    if output_cfg.get("save_json", True):
        json_path = out_dir / f"picks_{date_str}.json"
        json_path.write_text(json.dumps(full_output, indent=2))
        log.info("Saved JSON: %s", json_path)

        # Always overwrite latest.json — this is what the dashboard reads
        latest_path = out_dir / "latest.json"
        latest_path.write_text(json.dumps(full_output, indent=2))
        log.info("Updated latest.json")

    if output_cfg.get("save_markdown", True):
        md_path = out_dir / f"picks_{date_str}.md"
        lines = [f"# Top {len(picks)} Watchlist — {date_str}\n"]
        for rank, p in enumerate(picks, 1):
            lines.append(f"## #{rank} {p.ticker} — {p.name} (${p.price:.2f})")
            lines.append(f"**Score: {p.composite_score:.0f}/100** | {p.investment_rating} | Technical: {p.technical_grade} | Sentiment: {p.sentiment_grade}")
            if p.entry_zone:
                lines.append(f"- 🎯 Entry Zone: {p.entry_zone}")
            if p.target_1y:
                lines.append(f"- 📅 1-Year Target: {p.target_1y}")
            if p.target_3y:
                lines.append(f"- 🚀 3-Year Target: {p.target_3y}")
            lines.append("\n**Rationale:**")
            for b in p.rationale_bullets:
                lines.append(f"- {b}")
            if p.risks:
                lines.append("\n**Risks:**")
                for r in p.risks:
                    lines.append(f"- {r}")
            lines.append("")
        md_path.write_text("\n".join(lines))
        log.info("Saved Markdown: %s", md_path)


# ─── Scheduled Job ────────────────────────────────────────────────────────────

def run_and_post(cfg: dict):
    try:
        picks, all_scored, macro = run_analysis(cfg)
        poster = DiscordPoster(cfg)
        save_output(picks, cfg, all_scored=all_scored, macro=macro)
        poster.post_watchlist(picks, datetime.now())
    except Exception as exc:
        log.exception("Critical error in run_and_post: %s", exc)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WatchlistBot — AI-Powered Daily Stock Picks")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--now", action="store_true", help="Run analysis immediately (skip scheduler)")
    parser.add_argument("--dry-run", action="store_true", help="Run analysis but don't post to Discord")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg.get("output", {}).get("log_level", "INFO"))

    log.info("WatchlistBot starting up...")
    log.info("Framework URL: %s", cfg.get("framework", {}).get("url", "N/A"))

    if args.now or args.dry_run:
        log.info("Running immediately (--now / --dry-run flag set)")
        picks, all_scored, macro = run_analysis(cfg)
        save_output(picks, cfg, all_scored=all_scored, macro=macro)
        if not args.dry_run:
            poster = DiscordPoster(cfg)
            poster.post_watchlist(picks, datetime.now())
        else:
            poster = DiscordPoster({"discord": {"webhook_url": ""}})
            poster._print_to_console(picks, datetime.now())
        return

    # ── Scheduled mode ───────────────────────────────────────────────────────
    sched_cfg = cfg.get("schedule", {})
    post_time = sched_cfg.get("post_time", "09:30")
    tz_name = sched_cfg.get("timezone", "America/New_York")
    tz = pytz.timezone(tz_name)

    analysis_offset_h = sched_cfg.get("analysis_window_hours", 12)
    analysis_time_naive = datetime.strptime(post_time, "%H:%M") - timedelta(hours=analysis_offset_h)
    analysis_time_str = analysis_time_naive.strftime("%H:%M")

    log.info("Scheduled: Analysis at %s %s, Post at %s %s", analysis_time_str, tz_name, post_time, tz_name)

    # Schedule analysis run (starts gathering data N hours before post)
    analysis_results = []
    analysis_all_scored = []
    analysis_macro = None

    def _analysis_job():
        nonlocal analysis_results, analysis_all_scored, analysis_macro
        log.info("Starting scheduled analysis...")
        try:
            analysis_results, analysis_all_scored, analysis_macro = run_analysis(cfg)
            save_output(analysis_results, cfg, all_scored=analysis_all_scored, macro=analysis_macro)
        except Exception as exc:
            log.exception("Analysis job failed: %s", exc)

    def _post_job():
        if not analysis_results:
            log.warning("No analysis results ready — running now...")
            _analysis_job()
        poster = DiscordPoster(cfg)
        poster.post_watchlist(analysis_results, datetime.now(tz))

    schedule.every().day.at(analysis_time_str).do(_analysis_job)
    schedule.every().day.at(post_time).do(_post_job)

    log.info("Scheduler running. Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()

"""
bot.py — @VisionariesOnly Watchlist Bot (GitHub Actions Edition)
────────────────────────────────────────────────────────────────
Designed to run as a single GitHub Actions job:

  1. Builds a dynamic universe of 1,000+ tickers
  2. Scans ALL of them in parallel batches (takes ~2-3 hours)
  3. Immediately posts the top picks to Discord
  4. Saves output/latest.json for the dashboard

Triggered by GitHub Actions at 6:30 AM ET Mon-Fri.
Finishes scanning ~9:00-9:30 AM ET and posts to Discord.
Total runtime fits within GitHub's 6-hour free tier limit.

Usage:
  python bot.py                    # full scan + post (production)
  python bot.py --dry-run          # full scan, print to console only
  python bot.py --post-now         # skip scan, post whatever is in output/latest.json
  python bot.py --limit 100        # scan only first 100 tickers (for testing)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
os.chdir(_root)

from src.framework_loader import load_framework
from src.data_fetcher import DataFetcher
from src.sentiment_analyzer import SentimentAnalyzer, SentimentScore
from src.scoring_engine import ScoringEngine, ScoredStock, get_rating
from src.discord_poster import DiscordPoster
from src.macro_analyzer import MacroAnalyzer
from src.universe import get_universe


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M')}.log"),
        ],
    )

log = logging.getLogger("WatchlistBot")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ── Determine pool for a ticker ───────────────────────────────────────────────

def get_pool(ticker: str, config: dict) -> str:
    gems = set(config.get("universe", {}).get("hidden_gems", []))
    return "hidden_gem" if ticker in gems else "mainstream"


# ── Score a single ticker (safe wrapper) ─────────────────────────────────────

def score_one(
    ticker: str,
    fetcher: DataFetcher,
    sentiment_analyzer: SentimentAnalyzer,
    scorer: ScoringEngine,
    macro,
    config: dict,
) -> Optional[ScoredStock]:
    try:
        sd = fetcher.fetch(ticker)
        if not sd or sd.error:
            return None

        universe_cfg = config.get("universe", {})
        if sd.avg_daily_volume < universe_cfg.get("min_avg_volume", 0):
            return None
        if sd.fundamentals.market_cap < universe_cfg.get("min_market_cap", 0):
            return None

        sent = sentiment_analyzer.analyze(ticker, sd.name)
        result = scorer.score(sd, sent)

        # Attach mention count to result so the gem classifier can read it
        result._mention_count = sent.mention_count

        f = sd.fundamentals
        sector_bonus = (
            3.0 if f.sector in macro.best_sectors
            else -3.0 if f.sector in macro.avoid_sectors
            else 0.0
        )
        result.composite_score = max(0, min(100,
            result.composite_score + macro.score_adjustment + sector_bonus
        ))
        result.investment_rating = get_rating(result.composite_score)
        return result

    except Exception as exc:
        log.debug("[%s] Error: %s", ticker, exc)
        return None


# ── Full Universe Scan ────────────────────────────────────────────────────────

def run_full_scan(config: dict, limit: Optional[int] = None) -> Tuple[List[dict], List[dict], List[dict]]:
    """
    Scans the entire universe, returns (mainstream_picks, gem_picks, all_scores).
    All picks are plain dicts ready to be saved to JSON and sent to Discord.
    """
    log.info("═══════════════════════════════════════════════════════")
    log.info("  @VisionariesOnly Watchlist Bot — Full Universe Scan")
    log.info("═══════════════════════════════════════════════════════")
    start_time = time.time()

    # Load framework
    framework = load_framework(config.get("framework", {}).get("url", ""))
    log.info("Framework: %d questions", len(framework))

    # Build universe — returns all tickers + the russell-only set
    tickers, russell_only_set = get_universe(config)
    if limit:
        tickers = tickers[:limit]
        log.info("Limiting to first %d tickers (--limit flag)", limit)
    log.info("Universe: %d tickers | %d potential gem candidates", len(tickers), len(russell_only_set))

    # Macro context
    try:
        macro = MacroAnalyzer(config).get_context()
        log.info("Macro: %s | adj %+.1f | VIX %.1f",
                 macro.market_regime, macro.score_adjustment, macro.vix)
    except Exception as e:
        log.warning("Macro fetch failed: %s — using defaults", e)
        from src.macro_analyzer import MacroContext
        macro = MacroContext()

    # Engines
    fetcher = DataFetcher(config)
    sentiment_analyzer = SentimentAnalyzer(config)
    scorer = ScoringEngine(config, framework)

    # Thresholds for dynamic hidden gem classification
    GEM_MIN_SCORE    = 65    # must score at least this to qualify
    GEM_MAX_MENTIONS = 5     # must have fewer social mentions than this

    # Score all tickers in parallel batches
    all_results: List[Tuple[str, str, ScoredStock]] = []  # (ticker, pool, result)
    total = len(tickers)
    analyzed = 0
    scored_count = 0
    batch_size = 12  # parallel workers

    log.info("Starting scan with %d parallel workers...", batch_size)

    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]

        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {
                executor.submit(score_one, t, fetcher, sentiment_analyzer, scorer, macro, config): t
                for t in batch
            }
            for fut in as_completed(futures):
                ticker = futures[fut]
                analyzed += 1
                try:
                    result = fut.result()
                    if result:
                        # Dynamic hidden gem classification:
                        #   - Must be a Russell 1000 stock not in S&P 500/NASDAQ
                        #   - Must score >= 65 (good fundamentals)
                        #   - Must have < 5 social mentions (under the radar)
                        in_russell_only = ticker in russell_only_set
                        low_social = (result.sentiment_mentions if hasattr(result, 'sentiment_mentions')
                                      else getattr(result, '_mention_count', 0)) < GEM_MAX_MENTIONS
                        high_score = result.composite_score >= GEM_MIN_SCORE

                        pool = "hidden_gem" if (in_russell_only and high_score and low_social) else "mainstream"
                        all_results.append((ticker, pool, result))
                        scored_count += 1
                        log.debug("[%s] %.0f | %s | pool=%s mentions=%s",
                                  ticker, result.composite_score, result.investment_rating,
                                  pool, getattr(result, '_mention_count', '?'))
                except Exception as exc:
                    log.debug("[%s] Unhandled error: %s", ticker, exc)

        # Progress log every 100 tickers
        if analyzed % 100 == 0 or analyzed == total:
            elapsed = time.time() - start_time
            pct = analyzed / total * 100
            remaining_tickers = total - analyzed
            rate = analyzed / elapsed if elapsed > 0 else 1
            eta_mins = int(remaining_tickers / rate / 60)
            log.info("Progress: %d/%d (%.0f%%) | scored: %d | elapsed: %.0fm | ETA: ~%dm",
                     analyzed, total, pct, scored_count,
                     elapsed / 60, eta_mins)

        # Small delay to avoid hammering yfinance
        time.sleep(0.3)

    elapsed_total = time.time() - start_time
    log.info("Scan complete: %d/%d tickers scored in %.1f minutes",
             scored_count, total, elapsed_total / 60)

    # ── Sort and split by pool ────────────────────────────────────────────────
    mainstream_results = sorted(
        [(t, r) for t, pool, r in all_results if pool == "mainstream"],
        key=lambda x: x[1].composite_score, reverse=True
    )
    gem_results = sorted(
        [(t, r) for t, pool, r in all_results if pool == "hidden_gem"],
        key=lambda x: x[1].composite_score, reverse=True
    )
    all_sorted = sorted(all_results, key=lambda x: x[2].composite_score, reverse=True)

    output_cfg = config.get("output", {})
    n_main = output_cfg.get("top_n_mainstream", 5)
    n_gems = output_cfg.get("top_n_hidden_gems", 5)

    top_mainstream = [_to_dict(r, "mainstream") for _, r in mainstream_results[:n_main]]
    top_gems       = [_to_dict(r, "hidden_gem") for _, r in gem_results[:n_gems]]
    all_scores     = [{"ticker": t, "composite_score": round(r.composite_score, 2), "pool": pool}
                      for t, pool, r in all_sorted]

    log.info("Top mainstream: %s", [f"{p['ticker']}({p['composite_score']:.0f})" for p in top_mainstream])
    log.info("Top gems:       %s", [f"{p['ticker']}({p['composite_score']:.0f})" for p in top_gems])

    return top_mainstream, top_gems, all_scores


def _to_dict(result: ScoredStock, pool: str) -> dict:
    return {
        "ticker":             result.ticker,
        "name":               result.name,
        "composite_score":    round(result.composite_score, 2),
        "investment_rating":  result.investment_rating,
        "technical_grade":    result.technical_grade,
        "sentiment_grade":    result.sentiment_grade,
        "price":              result.price,
        "sector":             result.sector,
        "pool":               pool,
        "entry_zone":         result.entry_zone,
        "target_1y":          result.target_1y,
        "target_3y":          result.target_3y,
        "rationale_bullets":  result.rationale_bullets or [],
        "risks":              result.risks or [],
        "category_scores":    [
            {"category": c.category, "score": round(c.score, 3)}
            for c in (result.category_scores or [])
        ],
    }


# ── Save Output ───────────────────────────────────────────────────────────────

def save_output(
    mainstream: List[dict],
    gems: List[dict],
    all_scores: List[dict],
    config: dict,
    macro=None,
    scan_duration_mins: float = 0,
):
    Path("output").mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    macro_dict = {}
    if macro:
        try:
            macro_dict = {
                "yield10":     round(macro.rate_10yr, 2),
                "yieldCurve":  f"{macro.yield_curve_status.title()} ({macro.yield_curve_spread:+.2f})",
                "vix":         round(macro.vix, 1),
                "vixRegime":   macro.vix_regime.title(),
                "dxy":         round(macro.dxy, 1),
                "dxyTrend":    macro.dollar_trend.title(),
                "regime":      macro.regime_description,
            }
        except Exception:
            pass

    all_picks = mainstream + gems
    output = {
        "run_date":           date_str,
        "generated_at":       datetime.now().isoformat(),
        "mainstream_picks":   mainstream,
        "hidden_gem_picks":   gems,
        "picks":              all_picks,      # legacy field for dashboard
        "all_scores":         all_scores,
        "macro":              macro_dict,
        "scan_stats": {
            "tickers_in_universe": str(len(all_scores)),
            "analyzed_last_24h":   str(len(all_scores)),
            "current_cycle":       "1",
            "scan_duration_mins":  f"{scan_duration_mins:.1f}",
        },
        "meta": {
            "topScore":  round(all_picks[0]["composite_score"], 0) if all_picks else 0,
            "topTicker": all_picks[0]["ticker"] if all_picks else "—",
            "avgScore":  round(
                sum(p["composite_score"] for p in all_picks) / max(len(all_picks), 1), 0
            ),
            "tickers":   len(all_scores),
            "regime":    macro_dict.get("vixRegime", "—"),
            "vix":       str(macro_dict.get("vix", "—")),
            "runDate":   date_str,
        }
    }

    json_str = json.dumps(output, indent=2)
    Path(f"output/picks_{date_str}.json").write_text(json_str)
    Path("output/latest.json").write_text(json_str)
    log.info("Saved output/latest.json (%d mainstream + %d gems | %d total scored)",
             len(mainstream), len(gems), len(all_scores))


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="@VisionariesOnly Watchlist Bot")
    parser.add_argument("--config",    default="config.yaml")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Run full scan but print to console instead of posting to Discord")
    parser.add_argument("--post-now",  action="store_true",
                        help="Skip scan — post whatever is already in output/latest.json")
    parser.add_argument("--limit",     type=int, default=None,
                        help="Only scan first N tickers (useful for testing, e.g. --limit 50)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.get("output", {}).get("log_level", "INFO"))
    log.info("@VisionariesOnly Watchlist Bot starting")

    # ── Post-only mode ────────────────────────────────────────────────────────
    if args.post_now:
        latest = Path("output/latest.json")
        if not latest.exists():
            log.error("No output/latest.json found — run a full scan first")
            return
        data = json.loads(latest.read_text())
        mainstream = data.get("mainstream_picks", data.get("picks", [])[:5])
        gems       = data.get("hidden_gem_picks", data.get("picks", [])[5:10])
        poster = DiscordPoster(config)
        if args.dry_run:
            poster._print_to_console(mainstream, gems, datetime.now())
        else:
            poster.post_from_dicts(mainstream, gems, datetime.now())
        return

    # ── Full scan + post ──────────────────────────────────────────────────────
    scan_start = time.time()

    # Get macro once upfront so we can pass it to save_output
    try:
        macro = MacroAnalyzer(config).get_context()
    except Exception:
        macro = None

    mainstream, gems, all_scores = run_full_scan(config, limit=args.limit)
    scan_duration = (time.time() - scan_start) / 60
    log.info("Total scan time: %.1f minutes", scan_duration)

    save_output(mainstream, gems, all_scores, config, macro, scan_duration)

    if args.dry_run:
        log.info("Dry run — printing to console, skipping Discord")
        DiscordPoster({"discord": {"webhook_url": ""}})._print_to_console(
            mainstream, gems, datetime.now()
        )
    else:
        log.info("Posting to Discord...")
        DiscordPoster(config).post_from_dicts(mainstream, gems, datetime.now())

    log.info("All done.")


if __name__ == "__main__":
    main()

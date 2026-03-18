"""
sentiment_analyzer.py
─────────────────────
Gathers social + news sentiment for a ticker from:
  - Reddit (r/wallstreetbets, r/stocks, r/investing)
  - Yahoo Finance RSS headlines
  - NewsAPI (optional)

Returns a SentimentScore dataclass per ticker.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests

log = logging.getLogger(__name__)

POSITIVE_WORDS = {
    "bullish", "buy", "moon", "growth", "surge", "soar", "beat", "strong",
    "upgrade", "record", "rally", "outperform", "positive", "breakout",
    "momentum", "accelerat", "partner", "win", "deal", "expand", "dominat",
    "revolutio", "breakthrough", "profit", "revenue", "contract", "award",
}
NEGATIVE_WORDS = {
    "bearish", "sell", "crash", "decline", "drop", "miss", "weak", "downgrade",
    "negative", "layoff", "lawsuit", "fraud", "debt", "loss", "disappointing",
    "warning", "risk", "concern", "investigate", "recall", "bankrupt",
    "short", "overvalued", "bubble",
}


@dataclass
class SentimentScore:
    ticker: str
    mention_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    score: float = 0.0          # -1 (very bearish) to +1 (very bullish)
    trending: bool = False
    sources: List[str] = field(default_factory=list)
    top_headlines: List[str] = field(default_factory=list)


class SentimentAnalyzer:
    def __init__(self, config: dict):
        self.cfg = config
        self.sent_cfg = config.get("sentiment", {})
        self.api_keys = config.get("api_keys", {})
        self.lookback_hours = self.sent_cfg.get("lookback_hours", 24)
        self.enabled = self.sent_cfg.get("enabled", True)
        self._cache: Dict[str, SentimentScore] = {}

    def analyze(self, ticker: str, company_name: str = "") -> SentimentScore:
        if not self.enabled:
            return SentimentScore(ticker=ticker)
        if ticker in self._cache:
            return self._cache[ticker]

        ss = SentimentScore(ticker=ticker)
        mentions: List[tuple[str, str]] = []  # (title, body)

        # ── Reddit ────────────────────────────────────────────────────────────
        sources = self.sent_cfg.get("sources", [])
        reddit_subs = []
        if "reddit_wsb" in sources:
            reddit_subs.append("wallstreetbets")
        if "reddit_stocks" in sources:
            reddit_subs.append("stocks")
        if "reddit_investing" in sources:
            reddit_subs.append("investing")

        for sub in reddit_subs:
            posts = self._fetch_reddit(sub, ticker, company_name)
            for p in posts:
                mentions.append(p)
            if posts:
                ss.sources.append(f"r/{sub}")

        # ── Yahoo Finance headlines ───────────────────────────────────────────
        if "news_headlines" in sources:
            headlines = self._fetch_yahoo_news(ticker)
            for h in headlines:
                mentions.append((h, ""))
            if headlines:
                ss.sources.append("Yahoo Finance")

        # ── NewsAPI ───────────────────────────────────────────────────────────
        news_key = self.api_keys.get("news_api_key", "")
        if news_key and len(news_key) > 10:
            news_items = self._fetch_newsapi(ticker, company_name, news_key)
            for item in news_items:
                mentions.append(item)
            if news_items:
                ss.sources.append("NewsAPI")

        # ── Score ─────────────────────────────────────────────────────────────
        ss.mention_count = len(mentions)
        for title, body in mentions:
            text = (title + " " + body).lower()
            p = sum(1 for w in POSITIVE_WORDS if w in text)
            n = sum(1 for w in NEGATIVE_WORDS if w in text)
            if p > n:
                ss.positive_count += 1
            elif n > p:
                ss.negative_count += 1
            else:
                ss.neutral_count += 1
            if len(ss.top_headlines) < 5 and title:
                ss.top_headlines.append(title[:120])

        total = ss.positive_count + ss.negative_count + ss.neutral_count
        if total > 0:
            ss.score = (ss.positive_count - ss.negative_count) / total
        ss.trending = ss.mention_count >= self.sent_cfg.get("min_mentions_threshold", 3)

        log.info("[%s] Sentiment: %.2f (%d mentions)", ticker, ss.score, ss.mention_count)
        self._cache[ticker] = ss
        return ss

    # ── Data Sources ──────────────────────────────────────────────────────────

    def _fetch_reddit(self, subreddit: str, ticker: str, name: str) -> List[tuple]:
        """Use Reddit's public JSON API (no auth required for read)."""
        results = []
        try:
            headers = {"User-Agent": self.api_keys.get("reddit_user_agent", "WatchlistBot/1.0")}
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": f"{ticker} OR {name}",
                "sort": "new",
                "limit": 25,
                "restrict_sr": "on",
                "t": "day",
            }
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get("data", {}).get("children", [])
                for p in posts:
                    pd_ = p.get("data", {})
                    title = pd_.get("title", "")
                    selftext = pd_.get("selftext", "")
                    created = pd_.get("created_utc", 0)
                    age_h = (time.time() - created) / 3600
                    if age_h <= self.lookback_hours:
                        results.append((title, selftext))
        except Exception as exc:
            log.debug("Reddit fetch failed for %s/%s: %s", subreddit, ticker, exc)
        return results

    def _fetch_yahoo_news(self, ticker: str) -> List[str]:
        """Pull headline titles from Yahoo Finance RSS feed."""
        headlines = []
        try:
            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                # Simple regex parse - no XML lib dependency
                titles = re.findall(r"<title>(.*?)</title>", resp.text, re.DOTALL)
                # Skip first two (feed title + channel title)
                for t in titles[2:]:
                    clean = re.sub(r"<[^>]+>", "", t).strip()
                    if clean and ticker.upper() in clean.upper() or len(clean) > 15:
                        headlines.append(clean[:120])
        except Exception as exc:
            log.debug("Yahoo news fetch failed for %s: %s", ticker, exc)
        return headlines[:10]

    def _fetch_newsapi(self, ticker: str, name: str, api_key: str) -> List[tuple]:
        results = []
        try:
            from_dt = (datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": f'"{ticker}" OR "{name}"',
                "from": from_dt,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": 20,
                "apiKey": api_key,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                articles = resp.json().get("articles", [])
                for a in articles:
                    title = a.get("title", "")
                    desc = a.get("description", "")
                    results.append((title, desc))
        except Exception as exc:
            log.debug("NewsAPI fetch failed: %s", exc)
        return results

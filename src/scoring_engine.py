"""
scoring_engine.py
─────────────────
Uses Claude (Anthropic API) to score each stock against the full
investment framework, incorporating technical signals, fundamentals,
sentiment, and macro context.

Returns a ScoredStock with a 0–100 composite score plus per-category
breakdowns and human-readable bullet rationale.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import anthropic

from .data_fetcher import StockData
from .framework_loader import FrameworkQuestion
from .sentiment_analyzer import SentimentScore

log = logging.getLogger(__name__)


@dataclass
class CategoryScore:
    category: str
    score: float         # 0.0 – 1.0
    max_questions: int
    answered_yes: int
    notes: str = ""


@dataclass
class ScoredStock:
    ticker: str
    name: str = ""
    composite_score: float = 0.0    # 0–100
    framework_score: float = 0.0    # raw framework points
    category_scores: List[CategoryScore] = field(default_factory=list)
    technical_grade: str = "C"      # A+ / A / B / C / D / F
    sentiment_grade: str = "Neutral"
    investment_rating: str = "Avoid"
    rationale_bullets: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    entry_zone: str = ""
    target_1y: str = ""
    target_3y: str = ""
    price: float = 0.0
    sector: str = ""
    error: Optional[str] = None


RATING_THRESHOLDS = [
    (85, "🔥 STRONG BUY"),
    (70, "✅ BUY"),
    (55, "👀 WATCH"),
    (40, "⚠️ SPECULATIVE"),
    (0,  "❌ AVOID"),
]


def get_rating(score: float) -> str:
    for threshold, label in RATING_THRESHOLDS:
        if score >= threshold:
            return label
    return "❌ AVOID"


class ScoringEngine:
    def __init__(self, config: dict, framework: List[FrameworkQuestion]):
        self.cfg = config
        self.framework = framework
        self.weights = config.get("weights", {})
        self.api_key = config.get("api_keys", {}).get("anthropic_api_key", "")
        self._client: Optional[anthropic.Anthropic] = None
        if self.api_key and len(self.api_key) > 10:
            self._client = anthropic.Anthropic(api_key=self.api_key)

    def score(
        self,
        stock: StockData,
        sentiment: SentimentScore,
    ) -> ScoredStock:
        ss = ScoredStock(
            ticker=stock.ticker,
            name=stock.name,
            price=stock.technicals.price,
            sector=stock.fundamentals.sector,
        )

        if stock.error:
            ss.error = stock.error
            ss.composite_score = 0.0
            return ss

        # ── Category-level scoring from rule engine ────────────────────────
        cat_scores = self._rule_based_scoring(stock, sentiment)
        ss.category_scores = list(cat_scores.values())
        framework_raw = sum(c.score * c.max_questions for c in ss.category_scores)
        framework_max = sum(c.max_questions for c in ss.category_scores)
        ss.framework_score = (framework_raw / framework_max * 100) if framework_max > 0 else 0

        # Technical grade
        ss.technical_grade = self._grade_technicals(stock)

        # Sentiment grade
        ss.sentiment_grade = self._grade_sentiment(sentiment)

        # ── AI enrichment (Claude) ─────────────────────────────────────────
        if self._client:
            try:
                ai_result = self._ai_score(stock, sentiment, cat_scores)
                ss.composite_score = ai_result.get("composite_score", ss.framework_score)
                ss.rationale_bullets = ai_result.get("bullets", [])[:8]
                ss.risks = ai_result.get("risks", [])[:3]
                ss.entry_zone = ai_result.get("entry_zone", "")
                ss.target_1y = ai_result.get("target_1y", "")
                ss.target_3y = ai_result.get("target_3y", "")
            except Exception as exc:
                log.warning("[%s] AI scoring failed, falling back to rule engine: %s", stock.ticker, exc)
                ss.composite_score = self._rule_composite(ss.framework_score, stock, sentiment)
                ss.rationale_bullets = self._rule_bullets(stock, sentiment, cat_scores)
        else:
            ss.composite_score = self._rule_composite(ss.framework_score, stock, sentiment)
            ss.rationale_bullets = self._rule_bullets(stock, sentiment, cat_scores)

        ss.investment_rating = get_rating(ss.composite_score)
        return ss

    # ── Rule-Based Category Scoring ───────────────────────────────────────────

    def _rule_based_scoring(
        self,
        stock: StockData,
        sentiment: SentimentScore,
    ) -> Dict[str, CategoryScore]:
        t = stock.technicals
        f = stock.fundamentals
        ins = stock.insider
        cats: Dict[str, CategoryScore] = {}

        def cat(name: str) -> CategoryScore:
            if name not in cats:
                q_count = sum(1 for q in self.framework if q.category == name and not q.optional)
                cats[name] = CategoryScore(category=name, score=0.0, max_questions=max(q_count, 1), answered_yes=0)
            return cats[name]

        def yes(name: str):
            c = cat(name)
            c.answered_yes += 1

        # Leadership & Team
        if ins.bullish:
            yes("leadership_team")
        if ins.insider_ownership_pct > 0.05:
            yes("leadership_team")
        if ins.institutional_ownership_pct > 0.60:
            yes("leadership_team")

        # Product & Market Fit
        TECH_SECTORS = {"Technology", "Communication Services", "Healthcare", "Consumer Discretionary"}
        if f.sector in TECH_SECTORS:
            yes("product_market_fit")
        if f.revenue_growth_yoy > 0.15:
            yes("product_market_fit")
        if f.gross_margin > 0.50:
            yes("product_market_fit")
        if f.operating_margin > 0.15:
            yes("product_market_fit")

        # Macro Environment
        MACRO_SAFE = {"Technology", "Healthcare", "Utilities"}
        if f.sector in MACRO_SAFE:
            yes("macro_environment")
        if f.debt_to_equity < 1.5:
            yes("macro_environment")

        # Financial Health
        if f.free_cash_flow > 0:
            yes("financial_health")
        if f.debt_to_equity < 1.0:
            yes("financial_health")
        if f.cash_on_hand > f.total_debt * 0.5:
            yes("financial_health")
        if f.current_ratio > 1.5:
            yes("financial_health")
        if f.return_on_equity > 0.15:
            yes("financial_health")

        # Narrative & Adoption
        if sentiment.trending:
            yes("narrative_adoption")
        if sentiment.score > 0.2:
            yes("narrative_adoption")
        if ins.institutional_ownership_pct > 0.50:
            yes("narrative_adoption")

        # Technicals & Entry Point
        max_from_high = self.cfg.get("technicals", {}).get("max_pct_from_52w_high", 0.35)
        if -max_from_high <= t.pct_from_52w_high <= -0.05:
            yes("technicals_entry")
        if t.above_200ema:
            yes("technicals_entry")
        if t.golden_cross:
            yes("technicals_entry")
        if t.volume_accumulation:
            yes("technicals_entry")
        if t.risk_reward_ratio > 2.0:
            yes("technicals_entry")

        # Governance & Stability
        if f.pe_ratio > 0:  # at least has earnings
            yes("governance_stability")
        if ins.recent_sells == 0 or ins.net_buying_last_6m > 0:
            yes("governance_stability")

        # Compute scores
        for name, c in cats.items():
            w_key = name.replace(" ", "_").lower()
            c.score = min(c.answered_yes / c.max_questions, 1.0)

        return cats

    # ── AI Scoring via Claude ─────────────────────────────────────────────────

    def _ai_score(
        self,
        stock: StockData,
        sentiment: SentimentScore,
        cat_scores: Dict[str, CategoryScore],
    ) -> dict:
        t = stock.technicals
        f = stock.fundamentals

        prompt = f"""You are a $1M/year professional stock analyst and quant trader.
Evaluate {stock.ticker} ({stock.name}) as a 1–5 year investment using the Master Investment Scoring Framework.

## Market Data
- Price: ${t.price:.2f}
- 52w High: ${t.price_52w_high:.2f} ({t.pct_from_52w_high:.1%} from high)
- 52w Low: ${t.price_52w_low:.2f}
- EMA 20/50/200: ${t.ema_20:.2f} / ${t.ema_50:.2f} / ${t.ema_200:.2f}
- RSI(14): {t.rsi_14:.1f}
- Volume ratio (recent vs 20d avg): {t.volume_ratio:.2f}x
- MACD Signal: {t.macd_signal}
- Trend: {t.trend_direction}
- Above 200 EMA: {t.above_200ema}
- Golden Cross: {t.golden_cross}
- Risk/Reward Ratio: {t.risk_reward_ratio:.1f}x

## Fundamentals
- Sector: {f.sector} | Industry: {f.industry}
- Market Cap: ${f.market_cap/1e9:.1f}B
- Revenue Growth YoY: {f.revenue_growth_yoy:.1%}
- Earnings Growth YoY: {f.earnings_growth_yoy:.1%}
- Gross Margin: {f.gross_margin:.1%}
- Operating Margin: {f.operating_margin:.1%}
- Free Cash Flow: ${f.free_cash_flow/1e9:.2f}B
- D/E Ratio: {f.debt_to_equity:.2f}
- Forward PE: {f.forward_pe:.1f}
- PEG Ratio: {f.peg_ratio:.2f}
- Analyst Target: ${f.analyst_target_price:.2f} ({f.analyst_rating})
- ROE: {f.return_on_equity:.1%}

## Sentiment
- 24h Mentions: {sentiment.mention_count}
- Sentiment Score: {sentiment.score:.2f} (-1 to +1)
- Trending: {sentiment.trending}
- Sources: {', '.join(sentiment.sources) or 'None'}
- Top Headlines: {'; '.join(sentiment.top_headlines[:3]) or 'None'}

## Rule-Engine Category Scores
{json.dumps({k: round(v.score, 2) for k, v in cat_scores.items()}, indent=2)}

## Business Description
{f.description[:400]}

## Your Task
Score this stock 0–100 and provide investment analysis. Be tough — only scores above 70 deserve a "BUY" recommendation.

Respond ONLY with valid JSON in this exact format (no preamble, no markdown):
{{
  "composite_score": <0-100 float>,
  "bullets": [
    "🔵 [Category]: <concise insight>",
    "🔵 ...",
    (5–8 bullets total, mix of fundamentals, technicals, narrative, macro)
  ],
  "risks": [
    "⚠️ <risk 1>",
    "⚠️ <risk 2>",
    "⚠️ <risk 3>"
  ],
  "entry_zone": "<price range or condition, e.g. '$120–$130 on pullback to 50 EMA'>",
  "target_1y": "<1-year price target with brief rationale>",
  "target_3y": "<3-year price target with brief rationale>"
}}"""

        response = self._client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    # ── Fallback Helpers ──────────────────────────────────────────────────────

    def _rule_composite(self, framework_score: float, stock: StockData, sentiment: SentimentScore) -> float:
        t = stock.technicals
        tech_bonus = 0.0
        if t.above_200ema:
            tech_bonus += 5
        if t.golden_cross:
            tech_bonus += 5
        if t.risk_reward_ratio > 2.5:
            tech_bonus += 5
        if t.rsi_14 < 45:
            tech_bonus += 3

        sent_bonus = sentiment.score * 8

        return min(100, framework_score * 0.75 + tech_bonus + sent_bonus)

    def _rule_bullets(
        self, stock: StockData, sentiment: SentimentScore, cat_scores: Dict[str, CategoryScore]
    ) -> List[str]:
        t = stock.technicals
        f = stock.fundamentals
        bullets = []

        if f.revenue_growth_yoy > 0.20:
            bullets.append(f"📈 Revenue growing {f.revenue_growth_yoy:.0%} YoY — strong top-line momentum")
        if f.free_cash_flow > 0:
            bullets.append(f"💰 Free cash flow positive (${f.free_cash_flow/1e9:.2f}B) — self-funding growth")
        if t.above_200ema and t.golden_cross:
            bullets.append(f"📊 Price above 200 EMA with golden cross — confirmed long-term uptrend")
        elif t.above_200ema:
            bullets.append(f"📊 Trading above 200 EMA — healthy long-term structure")
        if t.pct_from_52w_high < -0.15:
            bullets.append(f"🎯 {abs(t.pct_from_52w_high):.0%} off 52-week high — potential value entry")
        if t.risk_reward_ratio > 2.0:
            bullets.append(f"⚖️ Risk/reward ~{t.risk_reward_ratio:.1f}x — asymmetric setup")
        if sentiment.trending and sentiment.score > 0.1:
            bullets.append(f"🔥 Positive social momentum ({sentiment.mention_count} mentions, sentiment {sentiment.score:.2f})")
        if f.gross_margin > 0.60:
            bullets.append(f"🏆 Gross margin {f.gross_margin:.0%} — premium pricing power and moat")
        if f.analyst_rating in ("buy", "strongBuy") and f.analyst_target_price > t.price:
            upside = (f.analyst_target_price / t.price - 1) * 100
            bullets.append(f"🎯 Analyst consensus: {f.analyst_rating} with {upside:.0f}% upside to ${f.analyst_target_price:.0f}")

        return bullets[:8] if bullets else [f"🔵 Framework score: {sum(c.score for c in cat_scores.values()):.0f} pts across {len(cat_scores)} categories"]

    def _grade_technicals(self, stock: StockData) -> str:
        t = stock.technicals
        score = 0
        if t.above_200ema:
            score += 2
        if t.golden_cross:
            score += 2
        if t.rsi_14 < 50:
            score += 1
        if t.trend_direction == "uptrend":
            score += 2
        if t.volume_accumulation:
            score += 1
        if t.risk_reward_ratio > 2.5:
            score += 2
        if t.macd_signal in ("bullish", "bullish_cross"):
            score += 1
        grades = [(10, "A+"), (8, "A"), (6, "B"), (4, "C"), (2, "D"), (0, "F")]
        for threshold, grade in grades:
            if score >= threshold:
                return grade
        return "F"

    def _grade_sentiment(self, s: SentimentScore) -> str:
        if s.score > 0.4:
            return "Very Positive"
        elif s.score > 0.15:
            return "Positive"
        elif s.score > -0.15:
            return "Neutral"
        elif s.score > -0.4:
            return "Negative"
        else:
            return "Very Negative"

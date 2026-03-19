"""
discord_poster.py — @VisionariesOnly Watchlist Bot
────────────────────────────────────────────────────
Concise Discord output. Accepts either ScoredStock objects or
plain dicts (from the SQLite DB via score_db.get_top_picks).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Union

import requests

log = logging.getLogger(__name__)

EMBED_COLORS = {
    "STRONG BUY": 0x00FF88,
    "BUY":        0x44CC44,
    "WATCH":      0xFFAA00,
    "SPECULATIVE":0xFF6600,
    "AVOID":      0xFF3333,
}
TECH_EMOJI = {"A+": "🟢", "A": "🟢", "B": "🟡", "C": "🟡", "D": "🔴", "F": "🔴"}


def _as_dict(p) -> dict:
    """Normalise a ScoredStock object or plain dict to a consistent dict."""
    if isinstance(p, dict):
        return p
    # ScoredStock object
    return {
        "ticker": p.ticker,
        "name": p.name,
        "composite_score": p.composite_score,
        "investment_rating": p.investment_rating,
        "technical_grade": p.technical_grade,
        "sentiment_grade": p.sentiment_grade,
        "price": p.price,
        "sector": p.sector,
        "entry_zone": p.entry_zone,
        "target_1y": p.target_1y,
        "target_3y": p.target_3y,
        "rationale_bullets": p.rationale_bullets or [],
        "risks": p.risks or [],
    }


class DiscordPoster:
    def __init__(self, config: dict):
        self.webhook  = config.get("discord", {}).get("webhook_url", "")
        self.username = config.get("discord", {}).get("username", "@VisionariesOnly Watchlist Bot")
        self.avatar   = config.get("discord", {}).get("avatar_url", "")
        self.mention  = config.get("discord", {}).get("mention", "")

    # ── Called from bot.py with DB dicts ─────────────────────────────────────

    def post_from_dicts(self, mainstream: List[dict], gems: List[dict], run_date: datetime):
        return self.post_watchlist(mainstream, gems, run_date)

    # ── Main entry ────────────────────────────────────────────────────────────

    def post_watchlist(self, mainstream, gems, run_date: datetime) -> bool:
        mainstream = [_as_dict(p) for p in mainstream]
        gems       = [_as_dict(p) for p in gems]

        if not self.webhook or "YOUR_DISCORD" in self.webhook:
            self._print_to_console(mainstream, gems, run_date)
            return False

        ok = True

        # Header
        ok &= self._send({"embeds": [{
            "title": f"👁  VisionariesOnly Watchlist  •  {run_date.strftime('%b %d, %Y')}",
            "description": (
                f"Analyzed from a universe of **1,000+ stocks** over the past 24 hours\n"
                f"Scored: Leadership · Product · Macro · Financials · Narrative · Technicals\n"
                f"{'━'*38}"
            ),
            "color": 0x0099FF,
        }]})

        # Mainstream section
        ok &= self._send({"embeds": [{"title": "📈  Top 5 Mainstream Picks", "color": 0x00AAFF,
            "description": "Large & mid-cap stocks with highest framework scores"}]})
        for i, p in enumerate(mainstream, 1):
            ok &= self._send({"embeds": [self._pick_embed(i, p, gem=False)]})

        # Hidden gems section
        ok &= self._send({"embeds": [{"title": "💎  Top 5 Hidden Gem Picks", "color": 0xAA44FF,
            "description": "Under-the-radar high-conviction opportunities"}]})
        for i, p in enumerate(gems, 1):
            ok &= self._send({"embeds": [self._pick_embed(i, p, gem=True)]})

        # Footer
        ok &= self._send({"embeds": [{"description":
            "⚠️ *Algorithmic analysis only — not financial advice. Do your own due diligence.*",
            "color": 0x222222}]})

        return ok

    def _pick_embed(self, rank: int, p: dict, gem: bool) -> dict:
        score = p.get("composite_score") or 0
        rating_raw = (p.get("investment_rating") or "WATCH").upper()
        # Strip emoji prefix
        for prefix in ["🔥 ", "✅ ", "👀 ", "⚠️ ", "❌ "]:
            rating_raw = rating_raw.replace(prefix, "")
        rating_raw = rating_raw.strip()

        # Find matching color key
        color = 0x888888
        for key, val in EMBED_COLORS.items():
            if key in rating_raw:
                color = val
                break

        bar = "█" * round(score / 10) + "░" * (10 - round(score / 10))
        t_emoji = TECH_EMOJI.get(p.get("technical_grade", "B"), "⚪")
        prefix = "💎" if gem else "📈"

        # 3 bullets max, cleaned up
        bullets = ""
        for b in (p.get("rationale_bullets") or [])[:3]:
            clean = str(b).replace("🔵 ", "").replace("📈 ", "").replace("💰 ", "")
            if "]: " in clean:
                clean = clean.split("]: ", 1)[1]
            bullets += f"• {clean[:85]}\n"

        t1 = str(p.get("target_1y") or "—")[:25]
        t3 = str(p.get("target_3y") or "—")[:25]
        entry = str(p.get("entry_zone") or "—")[:55]

        desc = (
            f"`{bar}` **{round(score)}/100**  {rating_raw}\n"
            f"{t_emoji} Tech: **{p.get('technical_grade','?')}**"
            f"  |  📣 {p.get('sentiment_grade','Neutral')}"
            f"  |  💼 {p.get('sector','—')}\n"
            f"🎯 Entry: {entry}\n"
            f"🚀 1yr: **{t1}**  |  3yr: {t3}\n\n"
            f"{bullets}"
        )

        price = p.get("price") or 0
        return {
            "title": f"{prefix} #{rank}  {p.get('ticker','')}  —  ${price:.2f}"[:100],
            "description": desc[:2000],
            "color": color,
        }

    def _send(self, payload: dict) -> bool:
        payload["username"] = self.username
        if self.avatar:
            payload["avatar_url"] = self.avatar
        try:
            resp = requests.post(self.webhook, json=payload, timeout=15)
            resp.raise_for_status()
            return True
        except Exception as exc:
            body = ""
            try: body = exc.response.text[:200]
            except Exception: pass
            log.error("Discord send failed: %s %s", exc, body)
            return False

    def _print_to_console(self, mainstream, gems, run_date):
        print(f"\n{'═'*65}")
        print(f"  @VisionariesOnly Watchlist  —  {run_date.strftime('%b %d, %Y  %I:%M %p ET')}")
        print(f"{'═'*65}")
        for label, picks in [("📈 MAINSTREAM", mainstream), ("💎 HIDDEN GEMS", gems)]:
            print(f"\n  {label}")
            print(f"  {'─'*50}")
            for i, p in enumerate(picks, 1):
                score = p.get("composite_score", 0)
                bar = "█" * round(score/10) + "░" * (10 - round(score/10))
                price = p.get("price", 0)
                print(f"\n  #{i}  {p.get('ticker')}  ${price:.2f}")
                print(f"      [{bar}] {round(score)}/100  {p.get('investment_rating','')}")
                print(f"      Tech:{p.get('technical_grade','?')}  Sent:{p.get('sentiment_grade','?')}  {p.get('sector','')}")
                if p.get("entry_zone"): print(f"      Entry: {p['entry_zone']}")
                if p.get("target_1y"):  print(f"      1yr: {p['target_1y']}")
                for b in (p.get("rationale_bullets") or [])[:3]:
                    print(f"      • {str(b)[:80]}")
        print(f"\n{'═'*65}\n")

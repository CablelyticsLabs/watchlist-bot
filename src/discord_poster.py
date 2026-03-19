"""
discord_poster.py
─────────────────
Formats ScoredStock results and POSTs them to a Discord webhook.

Message layout:
  • Header embed with date + market context
  • One embed per top pick with score, grade, bullets, entry zone, targets
  • Footer embed with risk disclaimer
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List

import requests

from .scoring_engine import ScoredStock

log = logging.getLogger(__name__)

EMBED_COLORS = {
    "🔥 STRONG BUY": 0x00FF88,  # green
    "✅ BUY": 0x44CC44,
    "👀 WATCH": 0xFFAA00,       # amber
    "⚠️ SPECULATIVE": 0xFF6600,
    "❌ AVOID": 0xFF3333,        # red
}

TECH_GRADE_EMOJI = {
    "A+": "🟢", "A": "🟢", "B": "🟡", "C": "🟡", "D": "🔴", "F": "🔴",
}


class DiscordPoster:
    def __init__(self, config: dict):
        self.webhook = config.get("discord", {}).get("webhook_url", "")
        self.mention = config.get("discord", {}).get("mention", "")
        self.username = config.get("discord", {}).get("username", "📈 WatchlistBot")
        self.avatar_url = config.get("discord", {}).get("avatar_url", "")

    def post_watchlist(self, picks: List[ScoredStock], run_date: datetime) -> bool:
        if not self.webhook or self.webhook == "YOUR_DISCORD_WEBHOOK_URL_HERE":
            log.warning("Discord webhook not configured — printing to stdout only")
            self._print_to_console(picks, run_date)
            return False

        # Post header first, then one embed per pick — avoids 6000 char Discord limit
        success = True

        # Header message
        header_payload = {
            "username": self.username,
            "embeds": [{
                "title": f"📊  Daily Top {len(picks)} Watchlist  •  {run_date.strftime('%A, %B %d %Y')}",
                "description": (
                    "Scored against the **Master Investment Scoring Framework** — "
                    "Leadership · Product · Macro · Financials · Narrative · Technicals · Governance\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ),
                "color": 0x0099FF,
                "timestamp": run_date.isoformat(),
            }]
        }
        if self.mention:
            header_payload["content"] = self.mention
        if self.avatar_url:
            header_payload["avatar_url"] = self.avatar_url

        try:
            resp = requests.post(self.webhook, json=header_payload, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            log.error("Discord header post failed: %s", exc)
            try:
                log.error("Discord response body: %s", exc.response.text if hasattr(exc, 'response') else "N/A")
            except Exception:
                pass
            return False

        # One message per pick
        for rank, pick in enumerate(picks, 1):
            pick_payload = {
                "username": self.username,
                "embeds": [self._build_pick_embed(rank, pick)]
            }
            if self.avatar_url:
                pick_payload["avatar_url"] = self.avatar_url
            try:
                resp = requests.post(self.webhook, json=pick_payload, timeout=15)
                resp.raise_for_status()
                log.info("Posted pick #%d %s to Discord", rank, pick.ticker)
            except Exception as exc:
                log.error("Discord pick #%d post failed: %s", rank, exc)
                try:
                    log.error("Discord response: %s", exc.response.text if hasattr(exc, 'response') else "N/A")
                except Exception:
                    pass
                success = False

        # Footer
        try:
            footer_payload = {
                "username": self.username,
                "embeds": [{
                    "description": "⚠️ *Algorithmic analysis only — not financial advice. Always do your own due diligence.*",
                    "color": 0x444444,
                }]
            }
            if self.avatar_url:
                footer_payload["avatar_url"] = self.avatar_url
            requests.post(self.webhook, json=footer_payload, timeout=15)
        except Exception:
            pass

        return success

    def _build_pick_embed(self, rank: int, pick: ScoredStock) -> dict:
        color = EMBED_COLORS.get(pick.investment_rating, 0x888888)
        tech_emoji = TECH_GRADE_EMOJI.get(pick.technical_grade, "⚪")

        bar_filled = round(pick.composite_score / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        description_lines = [
            f"**{pick.investment_rating}**  •  Score: `{pick.composite_score:.0f}/100`",
            f"`{bar}` {pick.composite_score:.0f}%",
            "",
        ]
        # Trim bullets to max 120 chars each, max 6 bullets
        for b in (pick.rationale_bullets or [])[:6]:
            description_lines.append(str(b)[:120])

        description = "\n".join(description_lines)[:4000]  # Discord embed desc limit

        fields = []
        if pick.entry_zone:
            fields.append({"name": "🎯 Entry Zone", "value": str(pick.entry_zone)[:1024], "inline": True})
        if pick.target_1y:
            fields.append({"name": "📅 1-Year Target", "value": str(pick.target_1y)[:1024], "inline": True})
        if pick.target_3y:
            fields.append({"name": "🚀 3-Year Target", "value": str(pick.target_3y)[:1024], "inline": True})

        fields.append({
            "name": "Grades",
            "value": (
                f"{tech_emoji} Technical: **{pick.technical_grade}**  "
                f"| 📣 Sentiment: **{pick.sentiment_grade}**  "
                f"| 💼 Sector: `{pick.sector}`"
            )[:1024],
            "inline": False,
        })

        if pick.risks:
            risks_text = "\n".join(str(r)[:100] for r in pick.risks[:3])
            fields.append({"name": "⚠️ Key Risks", "value": risks_text[:1024], "inline": False})

        return {
            "title": f"#{rank}  {pick.ticker}  —  {pick.name}  •  ${pick.price:.2f}"[:256],
            "description": description,
            "color": color,
            "fields": fields,
        }

    def _build_payload(self, picks: List[ScoredStock], run_date: datetime) -> dict:
        embeds = []

        # ── Header ────────────────────────────────────────────────────────────
        header = {
            "title": f"📊  Daily Top {len(picks)} Watchlist  •  {run_date.strftime('%A, %B %d %Y')}",
            "description": (
                "Scored against the **Master Investment Scoring Framework** — "
                "Leadership · Product · Macro · Financials · Narrative · Technicals · Governance\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            "color": 0x0099FF,
            "timestamp": run_date.isoformat(),
        }
        embeds.append(header)

        # ── Per-pick embeds ───────────────────────────────────────────────────
        for rank, pick in enumerate(picks, 1):
            color = EMBED_COLORS.get(pick.investment_rating, 0x888888)
            tech_emoji = TECH_GRADE_EMOJI.get(pick.technical_grade, "⚪")

            # Score bar visual
            bar_filled = round(pick.composite_score / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)

            description_lines = [
                f"**{pick.investment_rating}**  •  Score: `{pick.composite_score:.0f}/100`",
                f"`{bar}` {pick.composite_score:.0f}%",
                "",
            ]
            if pick.rationale_bullets:
                description_lines.extend(pick.rationale_bullets)

            fields = []
            if pick.entry_zone:
                fields.append({"name": "🎯 Entry Zone", "value": pick.entry_zone, "inline": True})
            if pick.target_1y:
                fields.append({"name": "📅 1-Year Target", "value": pick.target_1y, "inline": True})
            if pick.target_3y:
                fields.append({"name": "🚀 3-Year Target", "value": pick.target_3y, "inline": True})

            # Grade row
            fields.append({
                "name": "Grades",
                "value": (
                    f"{tech_emoji} Technical: **{pick.technical_grade}**  "
                    f"| 📣 Sentiment: **{pick.sentiment_grade}**  "
                    f"| 💼 Sector: `{pick.sector}`"
                ),
                "inline": False,
            })

            if pick.risks:
                fields.append({
                    "name": "⚠️ Key Risks",
                    "value": "\n".join(pick.risks),
                    "inline": False,
                })

            embed = {
                "title": f"#{rank}  {pick.ticker}  —  {pick.name}  •  ${pick.price:.2f}",
                "description": "\n".join(description_lines),
                "color": color,
                "fields": fields,
            }
            embeds.append(embed)

        # ── Footer ────────────────────────────────────────────────────────────
        footer_embed = {
            "description": (
                "⚠️ *This is algorithmic analysis only — not financial advice. "
                "Always do your own due diligence. Past performance does not guarantee future results.*"
            ),
            "color": 0x444444,
        }
        embeds.append(footer_embed)

        payload = {
            "username": self.username,
            "embeds": embeds,
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        if self.mention:
            payload["content"] = self.mention

        return payload

    def _print_to_console(self, picks: List[ScoredStock], run_date: datetime):
        """Fallback: pretty-print to stdout when Discord is not configured."""
        print(f"\n{'═'*70}")
        print(f"  📊  DAILY TOP {len(picks)} WATCHLIST  —  {run_date.strftime('%A, %B %d %Y')}")
        print(f"{'═'*70}")
        for rank, pick in enumerate(picks, 1):
            bar_filled = round(pick.composite_score / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)
            print(f"\n#{rank}  {pick.ticker}  —  {pick.name}  •  ${pick.price:.2f}")
            print(f"   Score: [{bar}] {pick.composite_score:.0f}/100  {pick.investment_rating}")
            print(f"   Technical: {pick.technical_grade}  |  Sentiment: {pick.sentiment_grade}  |  Sector: {pick.sector}")
            if pick.entry_zone:
                print(f"   Entry Zone: {pick.entry_zone}")
            if pick.target_1y:
                print(f"   1-Year Target: {pick.target_1y}")
            if pick.target_3y:
                print(f"   3-Year Target: {pick.target_3y}")
            print("   Rationale:")
            for b in pick.rationale_bullets:
                print(f"     {b}")
            if pick.risks:
                print("   Risks:")
                for r in pick.risks:
                    print(f"     {r}")
        print(f"\n{'═'*70}")
        print("⚠️  Not financial advice. Do your own due diligence.")
        print(f"{'═'*70}\n")

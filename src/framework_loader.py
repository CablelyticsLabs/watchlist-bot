"""
framework_loader.py
───────────────────
Fetches the scoring framework from the configured URL (Google Sheet CSV,
raw CSV, or JSON) and returns a structured list of scoring questions.
"""

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import List

import requests

log = logging.getLogger(__name__)


@dataclass
class FrameworkQuestion:
    category: str
    question: str
    weight: float = 1.0
    optional: bool = False


CATEGORY_ALIASES = {
    "leadership": "leadership_team",
    "leadership & team": "leadership_team",
    "product": "product_market_fit",
    "product & market fit": "product_market_fit",
    "macro": "macro_environment",
    "macro environment fit": "macro_environment",
    "financial": "financial_health",
    "financial health & profitability": "financial_health",
    "narrative": "narrative_adoption",
    "narrative & adoption": "narrative_adoption",
    "technicals": "technicals_entry",
    "technicals & entry point": "technicals_entry",
    "governance": "governance_stability",
    "governance & stability": "governance_stability",
    "crypto": "crypto_specific",
    "crypto-specific (optional)": "crypto_specific",
}


def normalize_category(raw: str) -> str:
    key = raw.strip().lower()
    return CATEGORY_ALIASES.get(key, key.replace(" ", "_"))


def load_framework(url: str) -> List[FrameworkQuestion]:
    """
    Download and parse the scoring framework from a URL.
    Supports:
      - Google Sheets CSV export
      - Raw CSV with columns: Category, Question
      - JSON list of {category, question} objects
    """
    log.info("Fetching framework from %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text

        if "json" in content_type or text.strip().startswith("["):
            return _parse_json(text)
        else:
            return _parse_csv(text)
    except Exception as exc:
        log.warning("Could not fetch framework from URL (%s). Using built-in default.", exc)
        return _default_framework()


def _parse_csv(text: str) -> List[FrameworkQuestion]:
    questions = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 2:
            continue
        cat_raw, question = row[0].strip(), row[1].strip()
        if not cat_raw or not question:
            continue
        # Skip header rows
        if cat_raw.lower() in ("category", "📊 master investment scoring framework", "how to use"):
            continue
        if question.lower().startswith(("fill in", "once answered", "if you are", "1.", "2.", "3.", "4.")):
            continue
        cat = normalize_category(cat_raw)
        optional = cat == "crypto_specific"
        questions.append(FrameworkQuestion(category=cat, question=question, optional=optional))
    log.info("Loaded %d questions from CSV framework", len(questions))
    return questions if questions else _default_framework()


def _parse_json(text: str) -> List[FrameworkQuestion]:
    data = json.loads(text)
    questions = []
    for item in data:
        cat = normalize_category(item.get("category", ""))
        q = item.get("question", "").strip()
        if cat and q:
            optional = cat == "crypto_specific"
            questions.append(FrameworkQuestion(category=cat, question=q, optional=optional))
    log.info("Loaded %d questions from JSON framework", len(questions))
    return questions if questions else _default_framework()


def _default_framework() -> List[FrameworkQuestion]:
    """Hard-coded fallback matching the Google Sheet."""
    return [
        # Leadership & Team
        FrameworkQuestion("leadership_team", "Has the CEO created something innovative that is well known within an industry before?"),
        FrameworkQuestion("leadership_team", "Has the CEO gone to a high-level school or held a leadership role at a top company?"),
        FrameworkQuestion("leadership_team", "Do the C-suite executives have prominent accolades?"),
        FrameworkQuestion("leadership_team", "Does the team have a history of delivering on roadmaps/promises?"),
        FrameworkQuestion("leadership_team", "Is insider/exec behavior bullish (more insider buying than selling)?"),
        # Product & Market Fit
        FrameworkQuestion("product_market_fit", "Are the products/services industry-leading?"),
        FrameworkQuestion("product_market_fit", "Do the products have the potential to change society?"),
        FrameworkQuestion("product_market_fit", "Do the products have a durable competitive moat?"),
        FrameworkQuestion("product_market_fit", "Is it easy to scale the business without losing money?"),
        FrameworkQuestion("product_market_fit", "Do they have strong marketing that connects with their key demographic?"),
        FrameworkQuestion("product_market_fit", "Does the company align with at least one key forward-looking trend?"),
        FrameworkQuestion("product_market_fit", "Does it fit into multiple mega trends (e.g., AI + cloud)?"),
        # Macro Environment
        FrameworkQuestion("macro_environment", "Does the company operate in a sector expected to outperform in the current macro environment?"),
        FrameworkQuestion("macro_environment", "Is the business resilient to rising interest rates or inflation?"),
        FrameworkQuestion("macro_environment", "Does government policy directly benefit their industry (subsidies, tax credits, etc.)?"),
        FrameworkQuestion("macro_environment", "Is the business model insulated from trade/tariff risks?"),
        # Financial Health
        FrameworkQuestion("financial_health", "Are they cash-flow positive?"),
        FrameworkQuestion("financial_health", "Do they have low or decreasing debt?"),
        FrameworkQuestion("financial_health", "Is debt-to-equity ratio manageable given today's interest rate environment?"),
        FrameworkQuestion("financial_health", "Does the company treasury hold valuable assets (cash, patents, IP)?"),
        FrameworkQuestion("financial_health", "Has the business weathered previous recessions/downcycles successfully?"),
        # Narrative & Adoption
        FrameworkQuestion("narrative_adoption", "Is there evidence of growing mainstream adoption or cultural momentum?"),
        FrameworkQuestion("narrative_adoption", "Is institutional or whale interest accumulating (funds, VCs)?"),
        FrameworkQuestion("narrative_adoption", "Is the project seeing active developer growth and ecosystem expansion?"),
        # Technicals & Entry Point
        FrameworkQuestion("technicals_entry", "Is the current price at an optimal level (not near ATH, ideally 20-30% off highs)?"),
        FrameworkQuestion("technicals_entry", "Is price close to long-term support (e.g., 200 EMA, multi-year base)?"),
        FrameworkQuestion("technicals_entry", "Has volume confirmed accumulation (institutional buying)?"),
        FrameworkQuestion("technicals_entry", "Is the current risk/reward favorable (clear upside vs limited downside)?"),
        FrameworkQuestion("technicals_entry", "Does the chart show a long-term uptrend (vs. multi-year decline)?"),
        # Governance & Stability
        FrameworkQuestion("governance_stability", "Does the company have transparent governance (no fraud, red flags)?"),
        FrameworkQuestion("governance_stability", "Are regulatory headwinds low (not under heavy government scrutiny)?"),
        # Crypto-Specific (Optional)
        FrameworkQuestion("crypto_specific", "Are tokenomics sustainable (low inflation, capped supply, strong burn mechanics)?", optional=True),
        FrameworkQuestion("crypto_specific", "Does the token integrate with or bridge into key ecosystems?", optional=True),
    ]

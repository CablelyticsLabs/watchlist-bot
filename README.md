# 👁 VisionariesOnly Watchlist Bot

An AI-powered stock analysis bot that scans 1,200+ stocks every weekday morning, scores each one against a custom investment framework, and posts the top picks to Discord at market open. Runs entirely on GitHub Actions — no server, no cost.

---

## What It Does

Every weekday at **6:30 AM ET**, GitHub automatically wakes up the bot and runs a full market sweep:

1. Pulls a live universe of 1,200+ tickers from S&P 500, NASDAQ 100, and Russell 1000
2. Scores every stock 0–100 against your investment framework
3. Classifies results into **Mainstream Picks** and **Hidden Gem Picks**
4. Posts the top 5 from each category to Discord around **9:00–9:30 AM ET**
5. Updates the live dashboard automatically

Zero input needed from you. It just runs every weekday on its own.

---

## The Two Categories

### 📈 Mainstream Picks
Top-scoring stocks from the S&P 500 and NASDAQ 100. Well-known large and mid-cap companies with the strongest framework scores that day.

### 💎 Hidden Gem Picks
Dynamically discovered — not a hardcoded list. A stock qualifies as a hidden gem only if **all three** are true:
- It's in the Russell 1000 but **not** in the S&P 500 or NASDAQ 100 (smaller, less-covered)
- It scores **65+** on the framework (genuinely strong fundamentals)
- It has **fewer than 5 social mentions** in the last 24 hours (not yet on anyone's radar)

---

## How the Scoring Works

The bot answers every question in your Google Sheet framework with a Yes or No based on real data, then scores across 7 categories:

| Category | Weight | What It Checks |
|---|---|---|
| **Technicals & Entry** | 20% | EMA structure, RSI, MACD, volume, golden cross, risk/reward ratio |
| **Product & Market Fit** | 18% | Revenue growth, gross margin, sector trends, competitive moat |
| **Financial Health** | 16% | Free cash flow, debt/equity, cash reserves, ROE |
| **Narrative & Adoption** | 14% | Social sentiment, institutional ownership, analyst ratings |
| **Macro Environment** | 12% | Market regime, sector tailwinds, rate resilience |
| **Leadership & Team** | 12% | Insider buying/selling, institutional ownership, exec track record |
| **Governance & Stability** | 8% | Earnings quality, insider behavior, regulatory risk |

After category scoring, **Claude AI** synthesizes everything holistically and produces a final 0–100 composite score plus bullet rationale, entry zone, and 1yr/3yr price targets. A macro adjustment of ±15 points is then applied based on current VIX, yield curve, and market regime.

**Score interpretation:**
- 85+ = 🔥 Strong Buy
- 70+ = ✅ Buy
- 55+ = 👀 Watch
- 40+ = ⚠️ Speculative
- Below 40 = ❌ Avoid

---

## File Structure

```
watchlist-bot/
├── bot.py                        # Main orchestrator — runs the full scan
├── config.yaml                   # All settings (edit this)
├── dashboard.html                # Live web dashboard (hosted on GitHub Pages)
├── requirements.txt              # Python dependencies
├── .github/
│   └── workflows/
│       └── daily_watchlist.yml   # GitHub Actions schedule (6:30 AM ET Mon-Fri)
├── src/
│   ├── universe.py               # Builds 1,200+ ticker universe dynamically
│   ├── data_fetcher.py           # Pulls price, technicals, fundamentals, insider data
│   ├── sentiment_analyzer.py     # Reddit + Yahoo Finance sentiment scoring
│   ├── scoring_engine.py         # Framework scoring + Claude AI synthesis
│   ├── macro_analyzer.py         # VIX, yield curve, market regime analysis
│   ├── discord_poster.py         # Formats and sends Discord messages
│   ├── framework_loader.py       # Fetches questions from your Google Sheet
│   └── score_db.py               # SQLite score persistence
├── output/                       # Bot writes latest.json here (dashboard reads it)
└── data/                         # Universe cache and local DB
```

---

## Setup

### 1. Fork or push this repo to GitHub

Make sure the folder structure is intact — especially `.github/workflows/daily_watchlist.yml`.

### 2. Add your secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | Required | Where to get it |
|---|---|---|
| `DISCORD_WEBHOOK` | ✅ Yes | Discord channel → Edit → Integrations → Webhooks |
| `ANTHROPIC_API_KEY` | ✅ Recommended | console.anthropic.com |
| `NEWS_API_KEY` | Optional | newsapi.org (free tier) |
| `REDDIT_CLIENT_ID` | Optional | reddit.com/prefs/apps |
| `REDDIT_CLIENT_SECRET` | Optional | same as above |

### 3. Enable GitHub Pages

Go to **Settings** → **Pages** → Source: **Deploy from branch** → Branch: `main` → Folder: `/ (root)`

Your dashboard will be live at:
```
https://YOUR-USERNAME.github.io/YOUR-REPO-NAME/dashboard.html
```

### 4. Test it

Go to **Actions** → **VisionariesOnly Watchlist Bot** → **Run workflow**

Set the **Limit** field to `50` for a quick 5-minute test run. Leave it blank for the full 1,200+ stock sweep.

---

## Configuration

Everything is controlled through `config.yaml`. Key settings:

```yaml
discord:
  webhook_url: "YOUR_WEBHOOK"          # Discord webhook URL
  username: "@VisionariesOnly Watchlist Bot"

framework:
  url: "YOUR_GOOGLE_SHEET_CSV_URL"     # Your scoring framework spreadsheet

universe:
  gem_min_score: 65                    # Minimum score to qualify as hidden gem
  gem_max_social_mentions: 5           # Max social mentions to qualify as hidden gem

output:
  top_n_mainstream: 5                  # How many mainstream picks to surface
  top_n_hidden_gems: 5                 # How many hidden gem picks to surface
```

To update your investment framework, just edit your Google Sheet. The bot fetches it fresh every run — no code changes needed.

---

## How to Trigger Manually

Go to **Actions** → **VisionariesOnly Watchlist Bot** → **Run workflow**

- Leave **Dry run** unchecked → full scan + Discord post
- Check **Dry run** → full scan, no Discord post (for testing)
- Set **Limit** to a number → only scan that many tickers (faster for testing)

---

## Schedule

The bot runs **Monday through Friday only**, starting at **6:30 AM ET** (11:30 AM UTC).

A full scan of 1,200+ stocks takes approximately 2.5–3 hours using 12 parallel workers, finishing around 9:00–9:30 AM ET — right at market open.

GitHub Actions free tier gives 2,000 minutes/month. This bot uses roughly 180 minutes/week (3 hours × 5 days), well within the free limit.

---

## Dashboard

The dashboard reads from `output/latest.json` which gets committed to the repo after every run. It shows:

- Top 5 mainstream picks + top 5 hidden gems (click any row to expand full thesis)
- Score distribution chart across all analyzed stocks
- Category radar chart for selected pick
- Macro environment panel (VIX, yield curve, DXY, market regime)
- Sector distribution
- Scan statistics (universe size, tickers analyzed, cycle progress)
- Countdown to next post

---

## Disclaimer

This bot is for educational and research purposes only. It does not constitute financial advice. Always do your own due diligence before making any investment decisions. Past performance does not guarantee future results.

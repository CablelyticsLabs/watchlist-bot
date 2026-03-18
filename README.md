# 📈 WatchlistBot — AI-Powered Daily Stock Intelligence

> *Beats most retail investors and many hedge funds by combining quantitative
> fundamentals, technical analysis, social sentiment, and Claude AI reasoning
> against a structured investment framework — delivered to Discord every morning
> at market open.*

---

## 🏗️ Architecture

```
watchlist-bot/
├── bot.py                   # Main entry point + scheduler
├── config.yaml              # 🔧 All configuration lives here
├── requirements.txt
├── src/
│   ├── framework_loader.py  # Fetches scoring questions from Google Sheet / CSV / JSON
│   ├── data_fetcher.py      # yfinance: price, fundamentals, insider activity, technicals
│   ├── sentiment_analyzer.py# Reddit, Yahoo RSS, NewsAPI sentiment
│   ├── scoring_engine.py    # Rule engine + Claude AI scoring
│   └── discord_poster.py    # Rich Discord embeds + console fallback
├── output/                  # JSON + Markdown pick reports
└── logs/                    # Daily rotating log files
```

---

## ⚡ Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `config.yaml`
```yaml
# Minimum required settings:
discord:
  webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK"

api_keys:
  anthropic_api_key: "sk-ant-..."   # For AI-powered analysis
```

### 3. Run immediately (test mode)
```bash
python bot.py --now --dry-run     # No Discord post, prints to console
python bot.py --now               # Runs now, posts to Discord
```

### 4. Run on schedule (production)
```bash
python bot.py                     # Runs analysis 12h before 9:30am ET, posts at 9:30am
```

### 5. (Optional) Run as a service
```bash
# systemd example — create /etc/systemd/system/watchlistbot.service
[Unit]
Description=WatchlistBot
After=network.target

[Service]
WorkingDirectory=/path/to/watchlist-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 🧠 The Scoring Framework (7 Categories)

| Category | Weight | What It Measures |
|---|---|---|
| **Leadership & Team** | 12% | CEO track record, insider buying, exec accolades |
| **Product & Market Fit** | 18% | Moat, scalability, megatrend alignment |
| **Macro Environment** | 12% | Sector tailwinds, rate resilience, policy support |
| **Financial Health** | 16% | FCF, debt, margins, recession resilience |
| **Narrative & Adoption** | 14% | Institutional accumulation, cultural momentum |
| **Technicals & Entry** | 20% | EMA structure, RSI, volume, risk/reward |
| **Governance & Stability** | 8% | Regulatory risk, transparency |

The framework is loaded dynamically from your configured URL — update it anytime without redeploying.

---

## 📊 Technical Indicators Calculated

| Indicator | Use |
|---|---|
| EMA 20/50/200 | Trend structure, support levels |
| RSI(14) | Overbought / oversold detection |
| MACD | Momentum crossover signals |
| Volume Ratio | Accumulation vs distribution |
| Golden Cross (50>200 EMA) | Long-term trend confirmation |
| 52-Week High/Low Distance | Entry timing |
| Risk/Reward Ratio | Upside to 52w high vs downside to 200 EMA |
| At-Support Detection | Optimal buy zone identification |

---

## 📡 Sentiment Sources

- **Reddit** — r/wallstreetbets, r/stocks, r/investing (public API, no auth needed)
- **Yahoo Finance RSS** — real-time headlines per ticker
- **NewsAPI** *(optional)* — broader news aggregation

---

## 🤖 AI Analysis (Claude Sonnet)

When an Anthropic API key is configured, each top candidate is sent to Claude with the full data package for a holistic score (0–100) plus:
- 5–8 concise investment thesis bullets
- 3 key risks
- Optimal entry zone
- 1-year and 3-year price targets

Without an API key, the rule-based engine provides solid scoring that still outperforms most retail analysis.

---

## 📬 Discord Output Example

```
📊 Daily Top 5 Watchlist — Wednesday, March 18 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#1 NVDA — NVIDIA Corporation • $875.40
Score: [████████░░] 84/100   🔥 STRONG BUY
🟢 Technical: A  |  📣 Sentiment: Very Positive  |  💼 Sector: Technology
🎯 Entry: $840–$870 on 50 EMA touch
📅 1-Year Target: $1,100 (+26%)
🚀 3-Year Target: $1,800–$2,200 (+100–150%)

• 📈 Revenue grew 122% YoY — datacenter GPU dominance
• 💰 FCF $21B — strongest balance sheet in semis
• 📊 Golden cross active, above 200 EMA — uptrend intact
• 🔥 Institutional accumulation at 65% ownership
• ⚖️ 3.2x risk/reward at current levels
• 🎯 Analyst consensus Buy, $1,050 mean target

⚠️ Key Risks:
⚠️ Valuation stretched (FWD PE 35x) — priced for perfection
⚠️ China export restrictions could impact ~20% of revenue
⚠️ AMD Instinct GPU competition accelerating
```

---

## ⚙️ Configuration Reference

| Key | Default | Description |
|---|---|---|
| `discord.webhook_url` | required | Discord channel webhook |
| `discord.mention` | "" | Role/user tag on post (e.g. `<@&ROLE_ID>`) |
| `framework.url` | Google Sheet | CSV/JSON URL of scoring questions |
| `schedule.post_time` | `09:30` | Daily post time (24hr, Eastern) |
| `schedule.analysis_window_hours` | `12` | Hours before post to start analysis |
| `universe.watchlist` | 20 tickers | Static ticker list |
| `universe.auto_discover` | `true` | Add high-momentum tickers automatically |
| `output.top_n` | `5` | Number of picks to surface |
| `weights.*` | Various | Per-category framework weights |
| `technicals.max_pct_from_52w_high` | `0.35` | Skip stocks within 5% of ATH |
| `fundamentals.min_revenue_growth_yoy` | `0.05` | Skip slow-growers |
| `api_keys.anthropic_api_key` | optional | Enables AI scoring layer |
| `api_keys.news_api_key` | optional | NewsAPI.org enrichment |

---

## 🔑 API Keys

| Service | Required? | Free Tier | Get At |
|---|---|---|---|
| yfinance | No (built-in) | Unlimited | — |
| Anthropic | Recommended | $5 free credit | anthropic.com |
| Reddit | No | Public JSON API | — |
| NewsAPI | No | 100 req/day | newsapi.org |
| Alpha Vantage | No | 25 calls/day | alphavantage.co |

---

## ⚠️ Disclaimer

This bot is for educational and research purposes only. It does not constitute financial advice. Always perform your own due diligence before making any investment decisions. Past performance does not guarantee future results.

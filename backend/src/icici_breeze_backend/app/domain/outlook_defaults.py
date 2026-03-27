"""Defaults for market outlook generation settings."""
from __future__ import annotations

DEFAULT_OUTLOOK_SYSTEM_PROMPT = (
    "You are a macro-strategist. Analyze geopolitical, business, and economic news "
    "specifically to identify 'Transmission Channels' to the market. For every news "
    "item, explain the impact on: 1) Cost of Capital (Interest rates), 2) Input Costs "
    "(Commodities), and 3) Investor Sentiment (Risk-on/Risk-off)."
)

DEFAULT_OUTLOOK_PROMPT_TEMPLATE = (
"Generate a structured market outlook with a focus on Geopolitical and Economic causality.\n"
    "Context:\n"
    "- scope: {scope}\n"
    "- symbol: {symbol}\n"
    "- english_only: true\n"
    "- current_date: 2026-03-27\n\n"
    "Rules:\n"
    "1) Identify the single most impactful Geopolitical or Economic driver from the sources.\n"
    "2) Explicitly state the 'Transmission Channel' to the {symbol} or general market.\n"
    "3) Return strict JSON only with no markdown.\n\n"
    "Required JSON schema:\n"
    "{required_schema_json}\n\n"
    "Source documents (JSON):\n"
    "{sources_json}\n"
)

DEFAULT_OUTLOOK_FEEDS: tuple[tuple[str, str], ...] = (
    ("Reuters Markets", "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC World News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol Business News", "https://www.moneycontrol.com/rss/business.xml"),
    ("Moneycontrol Markets News", "https://www.moneycontrol.com/rss/markets.xml"),
    ("Moneycontrol Top News", "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("Bloomberg Quint Economy", "https://www.bqprime.com/rss/economy-finance"),
    ("Financial Times Macro", "https://www.ft.com/?format=rss"),    
)

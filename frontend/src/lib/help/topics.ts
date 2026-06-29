import type { HelpCategory } from "@/lib/help/categories";

export type HelpTopic = {
  id: string;
  category: HelpCategory;
  title: string;
  summary: string;
  body: string[];
  keywords?: string[];
  relatedTopicIds?: string[];
};

export const helpTopics: HelpTopic[] = [
  {
    id: "login-flow",
    category: "account",
    title: "How do I log in?",
    summary:
      "Sign in with your app user ID and password, then complete ICICI broker login.",
    body: [
      "Use your Breeze Modern app user ID and password on the login page, then complete ICICI Direct broker authentication.",
      "After each ICICI login you must acknowledge the SEBI risk disclosure and Terms & Conditions before trading features unlock.",
      "If ICICI login fails, verify your API credentials under Settings → Broker Credentials and that your deployment static IP is registered with ICICI.",
    ],
    keywords: ["sign in", "icici", "session", "register"],
    relatedTopicIds: ["credentials", "risk-disclosure-login"],
  },
  {
    id: "risk-disclosure-login",
    category: "account",
    title: "Why do I see the risk disclosure every login?",
    summary:
      "SEBI-mandated derivatives risk disclosure is required after each ICICI session.",
    body: [
      "Breeze Modern shows the SEBI regulatory risk disclosure and Terms & Conditions after each successful ICICI login.",
      "This is required before you can use trading features. Read the disclosure and click Proceed to continue.",
      "If Proceed does not respond, refresh the page or clear your browser cache.",
    ],
    keywords: ["sebi", "disclosure", "proceed", "terms"],
    relatedTopicIds: ["login-flow"],
  },
  {
    id: "forgot-password",
    category: "account",
    title: "I forgot my app password",
    summary: "Reset your app password using ICICI authentication.",
    body: [
      "Go to Register → Forgot password and start recovery using your ICICI credentials.",
      "Complete the flow on Register → Recover complete after broker verification.",
      "You can also correct stored ICICI credentials under Settings → Broker Credentials without changing your app password.",
    ],
    keywords: ["reset", "recover", "password"],
    relatedTopicIds: ["login-flow", "credentials"],
  },
  {
    id: "license-status",
    category: "account",
    title: "License banner or read-only trading",
    summary:
      "Renew or activate your deployment license at breeze-ui.com when trading is blocked.",
    body: [
      "A banner at the top of the app indicates license status: expired, revoked, unlicensed, pending activation, or trial denied.",
      "When read-only, you cannot define strategies or execute trades until the license is active.",
      "Sign in at breeze-ui.com and follow the license console instructions for your deployment.",
      "Contact sales if your trial was already used for your ICICI User ID.",
    ],
    keywords: ["license", "expired", "revoked", "trial", "read-only"],
    relatedTopicIds: ["account-deletion"],
  },
  {
    id: "account-deletion",
    category: "account",
    title: "Deleting your Breeze Modern account",
    summary:
      "App deletion does not remove your ICICI account or AWS resources.",
    body: [
      "Settings → Delete Account permanently removes your profile and stored credentials from Breeze Modern only.",
      "Your ICICI Direct account is not affected.",
      "AWS resources tied to a license are not released automatically. Log in at breeze-ui.com and use the license console to release AWS resources.",
    ],
    keywords: ["delete", "aws", "remove"],
    relatedTopicIds: ["license-status"],
  },
  {
    id: "parked-orders",
    category: "orders",
    title: "Parked orders and after-hours placement",
    summary:
      "Orders placed when the market is closed are saved locally until you execute them from Order Book.",
    body: [
      "When the market is closed, the order confirm dialog only allows Park execution — nothing is sent to ICICI until you execute later.",
      "Parked orders appear under Order Book → Parked Execution. Edit quantity and price before executing.",
      "After the market opens, select parked rows and execute, or use Park execution during closed hours and fire them when the session starts.",
      "This replaces placing AMO orders directly to the broker from this app.",
    ],
    keywords: ["amo", "after hours", "park", "draft", "closed"],
    relatedTopicIds: ["market-hours", "aggressive-limit"],
  },
  {
    id: "aggressive-limit",
    category: "orders",
    title: "Aggressive limit orders",
    summary:
      "ICICI derives the limit price from LTP within the exchange daily price band.",
    body: [
      "Enable Aggressive limit or tap the lightning bolt on a leg. ICICI sets the limit from the last traded price (LTP) within the exchange daily price range.",
      "Buy orders use a higher price; sell orders use a lower price. You do not enter a manual limit.",
      "Orders may partially fill, remain pending, or be rejected depending on market conditions.",
    ],
    keywords: ["mkt", "market", "ltp", "limit"],
    relatedTopicIds: ["parked-orders", "chunk-splitting"],
  },
  {
    id: "clone-square-off",
    category: "orders",
    title: "Clone and square-off from Order Book",
    summary:
      "Reuse an existing order on Place Order with editable price and quantity.",
    body: [
      "From Order Book, use clone or square-off on a row.",
      "You are taken to Place Order with fields pre-filled from that order.",
      "Adjust price, quantity, and aggressive limit before confirming — the app does not re-lookup expiries or strikes from scrip master for clones.",
    ],
    keywords: ["clone", "square off", "copy"],
    relatedTopicIds: ["order-pages-compared"],
  },
  {
    id: "chunk-splitting",
    category: "orders",
    title: "Why orders split into multiple chunks",
    summary:
      "Large quantities are split per exchange freeze limits and optional chunk size.",
    body: [
      "The app breaks large orders into child orders based on Settings → Quantity Limits (per-scrip freeze/max) and lot size.",
      "In the order confirm dialog you can set chunk size for how many units go in each child order.",
      "Partial success is possible — some chunks may fill while others fail with broker errors.",
    ],
    keywords: ["chunk", "split", "freeze", "lot"],
    relatedTopicIds: ["quantity-limits", "aggressive-limit"],
  },
  {
    id: "rate-limit-429",
    category: "orders",
    title: "Broker rate limit (HTTP 429)",
    summary:
      "The app pauses and retries when ICICI returns too many requests.",
    body: [
      "If ICICI returns HTTP 429 or 503, a countdown overlay appears while the app waits before retrying.",
      "Avoid rapid repeated actions (bulk cancel, many chain refreshes) during the pause.",
      "Tune minimum seconds between API calls under Settings → API Usage.",
    ],
    keywords: ["429", "503", "throttle", "pause"],
    relatedTopicIds: ["api-usage-limits"],
  },
  {
    id: "basket-order",
    category: "orders",
    title: "Basket Order",
    summary:
      "Build custom multi-leg option baskets, view payoff, and execute all legs together.",
    body: [
      "Basket Order lets you pick buy/sell legs from the full option chain for one underlying and expiry.",
      "Simulate payoff and probability of profit before execution.",
      "Each leg supports manual limit price or aggressive limit (lightning bolt). Execute all legs in one confirm flow.",
      "Use Strategy Builder instead when you want the app to propose strategies from margin, PoP, and risk parameters.",
    ],
    keywords: ["basket", "multi-leg", "legs"],
    relatedTopicIds: ["order-pages-compared", "strategy-builder-overview"],
  },
  {
    id: "quote-sources",
    category: "quotes",
    title: "Quote source badges (Live, EOD, ICICI API)",
    summary:
      "Prices come from WebSocket during market hours, Bhavcopy after hours, or REST as fallback.",
    body: [
      "Live · WebSocket — prices and depth stream from ICICI Breeze WebSocket during market hours. Values refresh while you stay on the page.",
      "EOD · Bhavcopy — closing prices from NSE/BSE FO Bhavcopy after market hours. Open interest and depth reflect the last session, not live data.",
      "ICICI API — quotes fetched via REST when WebSocket or Bhavcopy is unavailable.",
    ],
    keywords: ["websocket", "bhavcopy", "live", "eod", "stale"],
    relatedTopicIds: ["market-hours", "reference-data-loads"],
  },
  {
    id: "market-hours",
    category: "quotes",
    title: "Market hours and when live quotes apply",
    summary:
      "Live execution and WebSocket quotes require an open market session.",
    body: [
      "The app uses your Exchange Calendar (Settings) to decide if the market is open.",
      "During open hours, option chains use WebSocket streaming. After close, chains switch to Bhavcopy closing prices.",
      "Order placement to ICICI is disabled when closed; use Park execution instead.",
    ],
    keywords: ["closed", "3:30", "weekend", "holiday"],
    relatedTopicIds: ["parked-orders", "exchange-calendar", "quote-sources"],
  },
  {
    id: "strategy-builder-overview",
    category: "strategy",
    title: "What Strategy Builder does",
    summary:
      "Proposes income and directional strategies from your margin, PoP, and risk inputs.",
    body: [
      "Choose Income, Bullish, or Bearish outlook and set margin to deploy, minimum probability of profit (PoP), and optional max loss.",
      "The engine proposes trades with editable legs, margin estimates, and payoff simulation.",
      "Build Your Own lets you pick legs manually from the full chain — similar to Basket Order but with strategy discovery modes.",
    ],
    keywords: ["income", "bullish", "bearish", "propose"],
    relatedTopicIds: ["probability-of-profit", "order-pages-compared"],
  },
  {
    id: "probability-of-profit",
    category: "strategy",
    title: "Probability of profit (PoP)",
    summary:
      "Model estimate of profitable outcome at expiry — not a guarantee.",
    body: [
      "PoP is the estimated chance the strategy is profitable at expiry, based on current spot, implied volatility, and days to expiry.",
      "For income strategies, your minimum PoP filters out trades below that threshold. Higher minimum pushes short strikes further OTM.",
      "For directional strategies, PoP is informational only. Conservative, Moderate, and Aggressive variants are chosen by conviction (delta, cost, liquidity).",
      "This is a model estimate, not a guarantee of outcome.",
    ],
    keywords: ["pop", "probability", "profit"],
    relatedTopicIds: ["strategy-builder-constraints"],
  },
  {
    id: "strategy-builder-constraints",
    category: "strategy",
    title: "No strategies returned",
    summary:
      "Relax PoP, return, margin, or max-loss constraints to see more results.",
    body: [
      "Tight filters (high min PoP, high min annualized return, low margin, strict max loss) can eliminate all passing trades.",
      "Check Near-threshold alternatives for trades that almost qualified.",
      "Leaving max loss blank (∞) allows unlimited-loss structures, with an extra confirmation step.",
      "Ensure reference data (Bhavcopy, SPAN) is loaded — stale data can affect margins and quotes.",
    ],
    keywords: ["empty", "no results", "filter"],
    relatedTopicIds: ["near-threshold-alternatives", "unlimited-loss"],
  },
  {
    id: "near-threshold-alternatives",
    category: "strategy",
    title: "Near-threshold alternatives",
    summary:
      "Trades that missed your PoP/return caps or were excluded due to max-loss limits.",
    body: [
      "These strategies did not meet your minimum PoP and/or annualized return thresholds.",
      "Unlimited-loss structures are excluded while a max-loss limit is set.",
      "They are the best available options if you choose to relax your constraints.",
    ],
    keywords: ["relaxed", "threshold", "alternative"],
    relatedTopicIds: ["strategy-builder-constraints", "unlimited-loss"],
  },
  {
    id: "unlimited-loss",
    category: "strategy",
    title: "Unlimited loss warning",
    summary:
      "Some strategies can lose more than your stated max loss — confirm before proceeding.",
    body: [
      "Naked shorts and similar structures can have large or unlimited loss beyond a numeric max-loss input.",
      "The app shows a risk warning before you apply such a trade.",
      "Set a max loss cap in filters to exclude these from primary results; they may still appear under Near-threshold alternatives.",
    ],
    keywords: ["naked", "short", "risk"],
    relatedTopicIds: ["strategy-builder-constraints"],
  },
  {
    id: "strategy-builder-margins",
    category: "strategy",
    title: "Strategy Builder margin estimates",
    summary:
      "Margins use SPAN baseline or Breeze API per your reference data settings.",
    body: [
      "Leg and net margins come from SPAN baseline sheets or ICICI Breeze margin API, configured under Settings → Reference Data Loads.",
      "Ensure SPAN baseline and scrip master are refreshed on schedule for accurate estimates.",
      "Displayed margin is indicative; ICICI RMS may differ at order time.",
    ],
    keywords: ["span", "margin", "baseline"],
    relatedTopicIds: ["reference-data-loads"],
  },
  {
    id: "setup-checklist",
    category: "settings",
    title: "First-time setup checklist",
    summary:
      "Configure credentials, limits, reference data, and calendar before trading.",
    body: [
      "1. Broker Credentials — ICICI API key and secret fragment.",
      "2. Quantity Limits — per-scrip freeze and max order quantities for chunking.",
      "3. Reference Data Loads — schedule Bhavcopy, scrip master, and SPAN baseline; choose margin source.",
      "4. Exchange Calendar — holidays and market hours for open/closed detection.",
    ],
    keywords: ["setup", "checklist", "first time"],
    relatedTopicIds: [
      "credentials",
      "quantity-limits",
      "reference-data-loads",
      "exchange-calendar",
    ],
  },
  {
    id: "credentials",
    category: "settings",
    title: "Broker credentials",
    summary: "Update ICICI API key and secret fragment.",
    body: [
      "Settings → Broker Credentials stores your ICICI Breeze API key and secret fragment encrypted on the server.",
      "Incorrect credentials cause ICICI login or API failures across the app.",
      "Use Register → Correct if you need to fix credentials during registration recovery.",
    ],
    keywords: ["api key", "secret", "icici"],
    relatedTopicIds: ["login-flow", "setup-checklist"],
  },
  {
    id: "quantity-limits",
    category: "settings",
    title: "Quantity limits",
    summary:
      "Per-scrip freeze quantities used when splitting large orders.",
    body: [
      "Settings → Quantity Limits defines max units per child order per scrip.",
      "Wrong values cause unexpected chunking or broker rejections.",
      "Defaults align with exchange freeze files; update when NSE/BSE publishes new limits.",
    ],
    keywords: ["freeze", "max quantity", "chunk"],
    relatedTopicIds: ["chunk-splitting", "setup-checklist"],
  },
  {
    id: "reference-data-loads",
    category: "settings",
    title: "Reference data loads",
    summary:
      "Bhavcopy, scrip master, and SPAN baseline for quotes and margins.",
    body: [
      "Schedule daily loads for NSE/BSE FO Bhavcopy, scrip master, and SPAN baseline.",
      "After-hours option chains and Strategy Builder margins depend on this data.",
      "Choose SPAN baseline vs Breeze API as the margin source for Strategy Builder.",
    ],
    keywords: ["bhavcopy", "scrip master", "span"],
    relatedTopicIds: ["quote-sources", "strategy-builder-margins", "setup-checklist"],
  },
  {
    id: "exchange-calendar",
    category: "settings",
    title: "Exchange calendar",
    summary:
      "Market hours and holidays for open/closed detection.",
    body: [
      "Settings → Exchange Calendar manages NSE/BSE holidays and session times.",
      "Incorrect calendar causes wrong market-closed banners or premature live trading attempts.",
      "You can sync from Breeze Console Admin Settings or edit locally.",
    ],
    keywords: ["holiday", "hours", "calendar"],
    relatedTopicIds: ["market-hours", "setup-checklist"],
  },
  {
    id: "api-usage-limits",
    category: "settings",
    title: "API usage and daily limits",
    summary:
      "Monitor ICICI REST call volume; ICICI caps at roughly 5,000 calls per IST day.",
    body: [
      "The header shows API calls today vs the daily cap.",
      "A warning dialog appears when you approach the limit. Review Settings → API Usage for details.",
      "Reduce heavy actions (repeated chain loads, bulk operations) near the cap.",
      "Configure rate-limit pause seconds to space out retries after 429/503.",
    ],
    keywords: ["api", "5000", "quota", "usage"],
    relatedTopicIds: ["rate-limit-429"],
  },
  {
    id: "genai-byok",
    category: "settings",
    title: "GenAI market outlook (BYOK)",
    summary:
      "Bring your own Gemini or OpenAI API key for dashboard outlook.",
    body: [
      "Settings → GenAI Settings stores your API key for market outlook features.",
      "Configure model fallbacks if the primary model fails.",
      "Outlook combines RSS headlines with your chosen LLM; failures show an error message on the dashboard.",
    ],
    keywords: ["gemini", "openai", "ai", "outlook"],
  },
  {
    id: "audit-logs",
    category: "settings",
    title: "Strategy Builder audit logs",
    summary:
      "Download recent propose-trades audit logs from your server.",
    body: [
      "Settings → Strategy Builder Audit Logs lists up to 10 recent propose-trades runs stored on your deployment.",
      "Use for troubleshooting why specific strategies were proposed or excluded.",
    ],
    keywords: ["audit", "log", "propose"],
    relatedTopicIds: ["strategy-builder-overview"],
  },
  {
    id: "breeze-api-playground",
    category: "settings",
    title: "Breeze API Playground",
    summary:
      "Raw ICICI REST calls with your live session — can place real orders.",
    body: [
      "Settings → Breeze API Playground fires raw ICICI Breeze REST APIs using your active session.",
      "This can place, modify, or cancel real orders. Use only if you understand the risk.",
      "Intended for API testing and debugging, not routine trading.",
    ],
    keywords: ["playground", "raw api", "test"],
  },
  {
    id: "portfolio-payoff",
    category: "product",
    title: "Portfolio payoff curve",
    summary:
      "Estimated P&L across underlying prices for current positions.",
    body: [
      "The Portfolio page shows a payoff curve visualization for your holdings.",
      "It estimates profit and loss at different underlying price levels based on current positions.",
      "For illustration; does not account for all broker fees or assignment scenarios.",
    ],
    keywords: ["payoff", "portfolio", "chart"],
  },
  {
    id: "mobile-responsive",
    category: "product",
    title: "Mobile and smaller screens",
    summary:
      "Layout is responsive; desktop is recommended for multi-leg trading.",
    body: [
      "Breeze Modern adapts to smaller screens including phones.",
      "Dense tables (option chain, multi-leg legs) are easier on desktop or tablet.",
      "Keyboard shortcuts (press ?) work on trading pages when not typing in a field.",
    ],
    keywords: ["iphone", "mobile", "responsive"],
  },
  {
    id: "what-is-breeze-modern",
    category: "product",
    title: "What is Breeze Modern?",
    summary:
      "Trading dashboard on ICICI Breeze API — not a full replacement for ICICI apps.",
    body: [
      "Breeze Modern is a browser-based portfolio and options trading dashboard using ICICI Direct Breeze APIs.",
      "It focuses on flows wired in this product: portfolio, orders, strategy tools, and settings on your infrastructure.",
      "It does not replace all features of ICICI's official mobile or web apps.",
    ],
    keywords: ["about", "breeze modern"],
  },
  {
    id: "order-pages-compared",
    category: "product",
    title: "Place Order vs Basket Order vs Strategy Builder",
    summary:
      "Pick the right page for single-leg, custom basket, or auto-proposed strategies.",
    body: [
      "Place Order — single-leg or simple manual orders with option chain.",
      "Basket Order — custom multi-leg baskets you design; payoff and execute together.",
      "Strategy Builder — systematic strategy discovery from margin, PoP, max loss, and outlook.",
    ],
    keywords: ["place order", "compare", "which page"],
    relatedTopicIds: ["basket-order", "strategy-builder-overview"],
  },
];

export function getTopicById(id: string): HelpTopic | undefined {
  return helpTopics.find((t) => t.id === id);
}

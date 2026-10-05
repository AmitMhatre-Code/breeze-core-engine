#!/usr/bin/env node
// Captures the user guide's screenshots (guide/user/images/{dark,light}/*.png) from a local
// instance running in MOCK broker mode:
//
//   MOCK_MARKET_MODE=LIVE ./dev.sh          # in one terminal (repo root)
//   node frontend/scripts/capture-guide-screenshots.mjs            # every shot, both themes
//   node frontend/scripts/capture-guide-screenshots.mjs --only place-order,order-confirm
//   node frontend/scripts/capture-guide-screenshots.mjs --theme dark
//
// Data is mock data. The two mock-only banners (mock broker, Next dev badge) are hidden, as is
// the spot's "· close" note (mock never ticks an index, so every spot there is a close), the
// license is reported active to the frontend, and a few endpoints mock mode cannot serve (market
// outlook, login disclosure) get fixed sample content. `--setup` first creates the state some shots
// need (parked orders, armed Profit Booking / Stop Loss rules) through the app's own UI.
//
// Never point this at a live instance: the setup phase places (parked) orders and arms rules.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.BASE_URL || "http://127.0.0.1:3000";
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = path.join(repoRoot, "guide", "user", "images");
const USER = { id: "GUIDEDEMO", password: "GuideDemo123" };
const DESKTOP = { width: 1440, height: 900 };

const args = process.argv.slice(2);
const argVal = (flag) => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : null;
};
const only = argVal("--only")?.split(",").map((s) => s.trim()).filter(Boolean) ?? null;
const themes = argVal("--theme") ? [argVal("--theme")] : ["dark", "light"];
const doSetup = args.includes("--setup");

const SAMPLE_OUTLOOK = {
  outlook_type: "market",
  as_of: new Date().toISOString(),
  english_only: true,
  disclaimer: "Machine-generated market commentary for information only. Not investment advice.",
  summary: [
    { category: "macro_global", text: "US markets closed mixed; the dollar index eased 0.3% and Brent held near $78." },
    { category: "macro_global", text: "Asian peers opened firmer after softer US inflation data." },
    { category: "macro_local", text: "FIIs were net buyers of ₹1,240 crore in cash; DIIs added ₹860 crore." },
    { category: "macro_local", text: "Banks and IT led yesterday's gains; metals lagged." },
    { category: "positioning", text: "NIFTY put writing concentrated at 23,900; the heaviest call open interest sits at 24,500." },
  ],
  inference: {
    volatility_view: "India VIX near 15 suggests a normal, range-bound session.",
    movement_scenarios: [
      "Holding above 24,000 keeps a drift towards 24,300 in play.",
      "A break below 23,900 would bring 23,700 into view.",
    ],
    confidence: "medium",
    caveats: ["Weekly expiry positioning can distort moves late in the session."],
  },
  strategy_ideas: [
    { tag: "Range", rationale: "An iron condor between 23,700 and 24,400 fits the open-interest range.", risk_note: "A trending day breaks the range quickly; size for the wing width." },
  ],
  sources: [
    { title: "Markets open higher as inflation cools", url: "https://example.com/markets", publisher: "Sample Newswire" },
    { title: "FII flows turn positive for the week", url: "https://example.com/flows", publisher: "Sample Business Daily" },
  ],
};

function log(...m) {
  console.log(...m);
}

async function newContext(browser, theme, viewport = DESKTOP) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  await ctx.addInitScript((t) => {
    try {
      localStorage.setItem("breeze-core-engine-theme", t);
    } catch {}
    const hide = () => {
      document.querySelectorAll("div[role=status]").forEach((el) => {
        if ((el.textContent || "").startsWith("Mock broker")) el.style.display = "none";
      });
    };
    const start = () => {
      const s = document.createElement("style");
      s.textContent = "nextjs-portal,[data-spot-note]{display:none!important}";
      document.head.appendChild(s);
      hide();
      new MutationObserver(hide).observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
  }, theme);
  await ctx.route("**/deployment/license-status", (r) =>
    r.fulfill({ json: { deployment_license_status: "active", deployment_license_read_only: false } }),
  );
  await ctx.route("**/api/outlook/market*", (r) => r.fulfill({ json: SAMPLE_OUTLOOK }));
  // A local instance has no portal, so no ICICI add-on rates: show the normal (rates received) state.
  await ctx.route("**/api/settings/margin-source/data*", async (r) => {
    const resp = await r.fetch();
    let j;
    try {
      j = await resp.json();
    } catch {
      return r.fulfill({ response: resp });
    }
    j.addon = {
      available: true,
      reason: null,
      message: null,
      received_at: new Date().toISOString(),
      rates: {
        version: "v3",
        index_rate: 0.02,
        index_deep_otm_rate: 0.03,
        index_deep_otm_threshold: 0.1,
        stock_rate: 0.039,
        stock_deep_otm_rate: 0.055,
        stock_deep_otm_threshold: 0.3,
        expiry_day_extra_rate: 0.02,
      },
    };
    return r.fulfill({ response: resp, json: j });
  });
  // Mock mode labels its simulated market clock; real deployments do not.
  for (const pattern of [
    "**/api/settings/market-status*",
    "**/dashboard/ws-health*",
    "**/bots/runs**",
    "**/api/signals**",
  ]) {
    await ctx.route(pattern, async (r) => {
      const resp = await r.fetch();
      const body = (await resp.text())
        .replace(/ \(simulated via MARKET_HOURS_OVERRIDE\)/g, "")
        .replace(/ ?Nothing fetched: this instance is in 'mock' mode[^.]*\.\s*/g, " ");
      return r.fulfill({ response: resp, body });
    });
  }
  // Mock mode's SENSEX quote is a placeholder (~118); show a realistic level instead.
  await ctx.route("**/dashboard/index-quotes*", async (r) => {
    const resp = await r.fetch();
    let j;
    try {
      j = await resp.json();
    } catch {
      return r.fulfill({ response: resp });
    }
    const s = j?.quotes?.sensex;
    if (s && s.ltp < 5000) Object.assign(s, { ltp: 79412.35, change: 238.6, change_pct: 0.3 });
    return r.fulfill({ response: resp, json: j });
  });
  return ctx;
}

async function login(page) {
  await page.request.post(`${BASE}/api/register/direct`, {
    data: { user_id: USER.id, password: USER.password, api_key: "sample-key", secret_fragment: "sample-secret" },
    failOnStatusCode: false,
  });
  const res = await page.request.post(`${BASE}/auth/direct-login`, {
    data: { user_id: USER.id, password: USER.password },
    failOnStatusCode: false,
  });
  if (res.status() !== 200) throw new Error(`mock login failed: ${res.status()} ${await res.text()}`);
  await page.request.put(`${BASE}/api/settings/telegram/onboarding-dismissed`, {
    data: { dismissed: true },
    failOnStatusCode: false,
  });
}

async function go(page, p, { wait = 2500, waitFor } = {}) {
  await page.goto(`${BASE}${p}`, { waitUntil: "domcontentloaded" });
  if (waitFor) await page.getByText(waitFor).first().waitFor({ timeout: 45_000 });
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
  await page.waitForTimeout(wait);
}

async function shoot(page, theme, name, target = "viewport") {
  const file = path.join(OUT, theme, `${name}.png`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await page.mouse.move(0, 0);
  if (target === "viewport") await page.screenshot({ path: file });
  else if (target === "full") await page.screenshot({ path: file, fullPage: true });
  else await target.screenshot({ path: file });
  log(`  ${theme}/${name}.png`);
}

/** Picks NIFTY and its nearest expiry on Place Order / Basket Order / Strategy Builder. */
async function pickNiftyNearestExpiry(page) {
  await page.getByRole("button", { name: "NIFTY", exact: true }).filter({ visible: true }).first().click();
  await page.waitForTimeout(1500);
  await page.getByLabel("Expiry date").filter({ visible: true }).first().click();
  await page
    .getByRole("listbox", { name: "Expiry dates" })
    .filter({ visible: true })
    .getByRole("option")
    .filter({ hasText: /\d/ })
    .first()
    .click();
  await page.waitForTimeout(6000);
}

const dialog = (page) => page.locator("[aria-modal='true']:visible").last();

/** Screenshot from a heading down to the bottom of its card (for panels with no wrapper of their own). */
async function shootFrom(page, theme, name, heading) {
  await heading.scrollIntoViewIfNeeded();
  const h = await heading.boundingBox();
  const card = await heading.locator("xpath=ancestor::section[1]").boundingBox();
  const file = path.join(OUT, theme, `${name}.png`);
  const top = h.y - 16;
  await page.screenshot({
    path: file,
    fullPage: true,
    clip: { x: card.x, y: top, width: card.width, height: card.y + card.height - top },
  });
  log(`  ${theme}/${name}.png`);
}

/** The bordered card around a sign-in form, padded, for the signed-out pages. */
async function shootCard(page, theme, name, inside) {
  const box = await inside.evaluate((el) => {
    let e = el;
    while (e && e !== document.body) {
      const c = String(e.className || "");
      const r = e.getBoundingClientRect();
      if (/\bborder\b/.test(c) && /rounded/.test(c) && r.width > 300 && r.width < 800) {
        return { x: r.x, y: r.y + window.scrollY, width: r.width, height: r.height };
      }
      e = e.parentElement;
    }
    return null;
  });
  if (!box) throw new Error(`no card found for ${name}`);
  const pad = 28;
  const file = path.join(OUT, theme, `${name}.png`);
  await page.screenshot({
    path: file,
    fullPage: true,
    clip: { x: box.x - pad, y: Math.max(0, box.y - pad), width: box.width + pad * 2, height: box.height + pad * 2 },
  });
  log(`  ${theme}/${name}.png`);
}

/** The page's main column (inside AppShell), for crops that drop the sidebar and header. */
const main = (page) => page.locator("#main-content");

// ---------------------------------------------------------------------------------------------
// Shots. Each gets a fresh page (and a fresh login when `auth` is true).
// ---------------------------------------------------------------------------------------------
const SHOTS = [
  // Getting started (signed out)
  ...[
    ["login", false, "/login"],
    ["register", false, "/register"],
    ["register-correct", false, "/register/correct"],
    ["recover-complete", false, "/register/recover-complete"],
    ["challenge", true, "/challenge?apisession=sample"],
  ].map(([name, auth, route]) => ({
    name,
    auth,
    run: async (p, t) => {
      await go(p, route);
      await shootCard(p, t, name, p.locator("form").first());
    },
  })),

  // Dashboard and frame
  {
    name: "dashboard",
    auth: true,
    run: async (p, t) => {
      await go(p, "/dashboard", { waitFor: "AI Market Outlook", wait: 6000 });
      await shoot(p, t, "dashboard-overview");
      await shoot(p, t, "app-frame");
      await shoot(p, t, "header-bar", p.locator("header").first());
      const tiles = main(p).locator("div.grid").first();
      await shoot(p, t, "dashboard-tiles", tiles);
      const market = p.locator("section:has(h2:text-is('NIFTY 50'))").locator("xpath=..");
      await shoot(p, t, "dashboard-market", market);
      await shoot(p, t, "dashboard-outlook", p.locator("section:has(h2:text-is('AI Market Outlook'))"));
    },
  },
  {
    name: "help-dialog",
    auth: true,
    run: async (p, t) => {
      await go(p, "/dashboard", { waitFor: "AI Market Outlook" });
      await p.keyboard.press("?");
      await p.getByText("Open the full user guide").waitFor();
      await p.waitForTimeout(600);
      await shoot(p, t, "help-dialog");
    },
  },
  {
    name: "mobile-menu",
    auth: true,
    viewport: { width: 390, height: 844 },
    run: async (p, t) => {
      await go(p, "/dashboard", { waitFor: "AI Market Outlook" });
      await p.getByRole("button", { name: "Open menu" }).click();
      await p.waitForTimeout(700);
      await shoot(p, t, "mobile-menu");
    },
  },

  // Pages
  { name: "performance", auth: true, run: async (p, t) => { await go(p, "/performance", { waitFor: "P&L statement", wait: 3000 }); await shoot(p, t, "performance", "full"); } },
  { name: "signals", auth: true, run: async (p, t) => { await go(p, "/signals", { waitFor: "Backtest every signal" }); await shoot(p, t, "signals", "full"); } },
  {
    name: "bots",
    auth: true,
    run: async (p, t) => {
      await go(p, "/bots", { waitFor: "Holdings Option Writer" });
      await shoot(p, t, "bots", "full");
      await shoot(p, t, "bot-card", p.locator("section.app-card:has(h2:text-is('Expiry-Day Index Writer'))"));
      await shoot(p, t, "bot-activity", p.locator("section:has(> h2:text-is('Activity')), div:has(> h2:text-is('Activity'))").last());
    },
  },

  // Settings
  ...[
    ["settings-credentials", "credentials", "full"],
    ["settings-quantity-limits", "quantity-limits", "viewport"],
    ["settings-trading-costs", "trading-costs", "full"],
    ["settings-liquidity-checks", "liquidity-checks", "full"],
    ["settings-api-usage", "api-usage", "full"],
    ["settings-reference-data", "reference-data-loads", "viewport"],
    ["settings-exchange-calendar", "exchange-calendar", "full"],
    ["settings-telegram", "telegram-alerts", "full"],
    ["settings-audit-logs", "audit-logs", "full"],
    ["settings-storage", "storage", "full"],
    ["settings-delete-account", "delete-account", "full"],
  ].map(([name, tab, target]) => ({
    name,
    auth: true,
    run: async (p, t) => {
      await go(p, `/settings?tab=${tab}`, { wait: 3500 });
      await shoot(p, t, name, target);
    },
  })),
  {
    name: "settings-margin-harness",
    auth: true,
    run: async (p, t) => {
      await go(p, "/settings?tab=reference-data-loads", { waitFor: "Margin comparison harness", wait: 2500 });
      const panel = p.getByRole("heading", { name: "Margin comparison harness" }).locator("xpath=../..");
      await shoot(p, t, "settings-margin-harness", panel);
    },
  },
  {
    name: "settings-api-playground",
    auth: true,
    run: async (p, t) => {
      await go(p, "/settings?tab=api-playground", { wait: 2500 });
      await shoot(p, t, "settings-api-playground");
    },
  },
  // Place Order and the confirmation dialog
  {
    name: "place-order",
    auth: true,
    run: async (p, t) => {
      await go(p, "/place-order", { wait: 1500 });
      await pickNiftyNearestExpiry(p);
      await p.getByPlaceholder("e.g. 65").fill("130");
      await p.getByPlaceholder("e.g. 65").blur();
      await p.waitForTimeout(2500);
      await shoot(p, t, "place-order");
      await p.getByRole("button", { name: "Sell", exact: true }).click();
      await dialog(p).getByText("Confirm execution").waitFor();
      await p.waitForTimeout(3000);
      await shoot(p, t, "order-confirm", dialog(p));
    },
  },
  {
    name: "basket-order",
    auth: true,
    run: async (p, t) => {
      await go(p, "/basket-order", { wait: 1500 });
      await pickNiftyNearestExpiry(p);
      await p.getByRole("button", { name: "options chain", exact: true }).click({ timeout: 120_000 });
      await p.waitForTimeout(2500);
      const chain = dialog(p);
      // A credit iron condor around the money. Mock prices are random but stable, so these
      // strikes are chosen to give the textbook shape (credit < wing width).
      for (const [side, right, strike] of [
        ["Buy", "Put", "22,600"],
        ["Sell", "Put", "22,850"],
        ["Sell", "Call", "23,300"],
        ["Buy", "Call", "23,550"],
      ]) {
        await chain.getByRole("button", { name: new RegExp(`^${side} ${right} ${strike}(\\.0+)?$`) }).first().click();
        await p.waitForTimeout(300);
      }
      await chain.getByRole("button", { name: "Close" }).filter({ visible: true }).first().click();
      await p.waitForTimeout(1500);
      await p.getByRole("button", { name: "Calculate Margins" }).click();
      await p.waitForTimeout(5000);
      await shoot(p, t, "basket-order", "full");
      await shootFrom(p, t, "basket-payoff", p.getByText("3. Payoff simulation", { exact: true }));
    },
  },
  {
    name: "strategy-builder",
    auth: true,
    run: async (p, t) => {
      await go(p, "/strategy-builder", { wait: 1500 });
      await pickNiftyNearestExpiry(p);
      const params = p.locator("#strategy-builder-parameters input[type=number]");
      for (const [i, v] of [[0, "5"], [1, "1.5"], [2, "65"], [3, "10"]]) await params.nth(i).fill(v);
      await p.getByRole("button", { name: /^Income Strategies/ }).waitFor({ timeout: 90_000 });
      await p.getByRole("button", { name: /^Income Strategies/ }).click();
      await p.getByText("Select trade").first().waitFor({ timeout: 180_000 });
      await p.waitForTimeout(3000);
      await shoot(p, t, "strategy-builder", "full");
    },
  },

  // Portfolio
  {
    name: "portfolio",
    auth: true,
    run: async (p, t) => {
      await go(p, "/portfolio", { waitFor: "Open positions", wait: 6000 });
      await shoot(p, t, "portfolio-overview", "full");
      await shoot(p, t, "portfolio-group-expanded", p.locator("tr.app-table-row:has-text('What-if')").first());
    },
  },
  {
    // A campaign's card on its NIFTY group. Adopts the mock NIFTY group as a campaign the first
    // time (mock data only), then shoots the section.
    name: "portfolio-condor-card",
    auth: true,
    run: async (p, t) => {
      await go(p, "/portfolio", { waitFor: "Open positions", wait: 4000 });
      const row = p.locator("tr[role=button]", { hasText: "NIFTY" }).first();
      if ((await row.getAttribute("aria-expanded")) !== "true") await row.click();
      const section = p.locator("section[aria-label='Dynamic Iron Condor']").first();
      await section.waitFor({ timeout: 20_000 });
      const adopt = section.getByRole("button", { name: "Adopt as a campaign…" });
      if (await adopt.isVisible().catch(() => false)) {
        await adopt.click();
        await section.getByRole("button", { name: /^Adopt NIFTY/ }).click();
      }
      await section.getByText("Evaluated now").waitFor({ timeout: 30_000 });
      await p.waitForTimeout(1500);
      await shoot(p, t, "portfolio-condor-card", section);
      // The Adjust ticket, previewed on Close all (nothing is executed).
      await section.getByRole("button", { name: "Adjust…" }).click();
      const ticket = dialog(p);
      await ticket.getByRole("button", { name: "Close all", exact: true }).click();
      await ticket.locator("input[type=number]").first().waitFor({ timeout: 20_000 });
      await ticket.getByRole("button", { name: "Preview" }).click();
      await ticket.getByText("Total credit after").waitFor({ timeout: 30_000 });
      await p.waitForTimeout(800);
      await shoot(p, t, "portfolio-condor-ticket", ticket);
      await ticket.getByRole("button", { name: "Close", exact: true }).click();
    },
  },
  {
    name: "portfolio-pbsl",
    auth: true,
    run: async (p, t) => {
      await go(p, "/portfolio", { waitFor: "Open positions", wait: 5000 });
      await p.getByRole("button", { name: "+ Set Profit Booking / Stop Loss", exact: true }).filter({ visible: true }).first().click();
      await dialog(p).getByText("Set P&L Profit Booking / Stop Loss").waitFor();
      await fillRule(dialog(p));
      await p.waitForTimeout(800);
      await shoot(p, t, "portfolio-pbsl", dialog(p));
    },
  },
  {
    name: "portfolio-square-off",
    auth: true,
    run: async (p, t) => {
      await go(p, "/portfolio", { waitFor: "Open positions", wait: 5000 });
      const expanded = p.locator("tr.app-table-row:has-text('What-if')").first();
      const groupRow = expanded.locator("xpath=preceding-sibling::tr[1]");
      await groupRow.getByRole("checkbox").filter({ visible: true }).first().click();
      await p.getByRole("button", { name: "Square Off Selected", exact: true }).filter({ visible: true }).first().click();
      await dialog(p).getByText("Square off").first().waitFor();
      await p.waitForTimeout(1500);
      await shoot(p, t, "portfolio-square-off", dialog(p));
    },
  },
  {
    name: "portfolio-leg-gtt",
    auth: true,
    run: async (p, t) => {
      await go(p, "/portfolio", { waitFor: "Open positions", wait: 4000 });
      await p.getByRole("switch", { name: "Group legs" }).click();
      await p.waitForTimeout(1500);
      await p.locator("tbody tr[role=button]").filter({ visible: true }).first().click();
      await p.waitForTimeout(1000);
      await p.getByRole("button", { name: "Profit Booking / Stop Loss", exact: true }).filter({ visible: true }).first().click();
      await dialog(p).getByText("Profit Booking / Stop Loss (this leg)").waitFor();
      await p.waitForTimeout(1500);
      await shoot(p, t, "portfolio-leg-gtt", dialog(p));
      await p.keyboard.press("Escape");
      await p.getByRole("switch", { name: "Group legs" }).click();
    },
  },

  // Order Book
  {
    name: "order-book",
    auth: true,
    run: async (p, t) => {
      await go(p, "/orders", { waitFor: "Parked execution", wait: 4000 });
      await shoot(p, t, "order-book", "full");
      await shoot(p, t, "order-book-parked", p.locator("section:has-text('Parked execution')").first());
      await shoot(p, t, "order-book-pbsl", p.locator("section:has-text('Rules armed from Portfolio')").first());
      await p.getByRole("button", { name: "Modify", exact: true }).filter({ visible: true }).first().click();
      await dialog(p).getByText("Modify").first().waitFor();
      await p.waitForTimeout(1500);
      await shoot(p, t, "order-book-modify", dialog(p));
    },
  },
  {
    name: "logout-confirm",
    auth: true,
    run: async (p, t) => {
      await go(p, "/dashboard", { waitFor: "AI Market Outlook" });
      await p.getByRole("button", { name: "Log out" }).click();
      await dialog(p).getByText("Log out?").waitFor();
      await p.waitForTimeout(2000);
      await shoot(p, t, "logout-confirm", dialog(p));
    },
  },
  {
    name: "risk-disclosure",
    auth: true,
    run: async (p, t) => {
      const md = fs.readFileSync(path.join(repoRoot, "docs", "login-terms.md"), "utf8");
      const doc = { version: 1, content_markdown: md, effective_date: "2026-04-01", portal_configured: true };
      await p.route("**/api/login-disclosure/current*", (r) => r.fulfill({ json: doc }));
      await go(p, "/dashboard", { wait: 4000 });
      await shoot(p, t, "risk-disclosure");
    },
  },

  // Bots
  {
    name: "bot-run-sheet",
    auth: true,
    run: async (p, t) => {
      await go(p, "/bots", { waitFor: "Holdings Option Writer", wait: 1500 });
      await p.getByRole("button", { name: "Start a run for Holdings Option Writer" }).click();
      await p.waitForTimeout(8000);
      await shoot(p, t, "bot-run-sheet");
    },
  },
  {
    name: "bot-backtest",
    auth: true,
    run: async (p, t) => {
      await go(p, "/bots", { waitFor: "Long Scalper", wait: 1500 });
      await p.locator("section.app-card:has(h2:text-is('Long Scalper'))").getByTitle("Backtest").click();
      await p.waitForTimeout(2000);
      await shoot(p, t, "bot-backtest", dialog(p));
    },
  },
];

async function fillRule(d) {
  const inputs = d.locator("input");
  await inputs.nth(0).fill("25000");
  await inputs.nth(1).fill("15000");
  await inputs.nth(2).fill("5");
  await inputs.nth(3).fill("5");
}

/** Creates the state some shots show: two parked orders and two armed PB/SL rules. Mock mode only. */
async function setup(browser) {
  log("setup: parked orders and armed rules");
  const ctx = await newContext(browser, "dark");
  const p = await ctx.newPage();
  await login(p);
  const parkedRes = await p.request.get(`${BASE}/book/parked-orders`, { failOnStatusCode: false });
  const parked = parkedRes.ok() ? ((await parkedRes.json()).orders ?? []) : [];
  for (const extra of parked.slice(2)) {
    await p.request.delete(`${BASE}/book/parked-orders/${encodeURIComponent(extra.id)}`);
  }
  for (const right of ["CE", "PE"].slice(Math.min(2, parked.length))) {
    await go(p, "/place-order", { wait: 1500 });
    await pickNiftyNearestExpiry(p);
    if (right === "PE") await p.getByRole("button", { name: /^Call \(CE\)/ }).click();
    await p.waitForTimeout(2500);
    await p.getByPlaceholder("e.g. 65").fill("130");
    await p.getByPlaceholder("e.g. 65").blur();
    await p.waitForTimeout(1500);
    await p.getByRole("button", { name: "Sell", exact: true }).click();
    await dialog(p).getByRole("button", { name: "Park execution" }).click();
    await p.waitForTimeout(2500);
    log(`  parked a NIFTY ${right} sell`);
  }
  await go(p, "/portfolio", { waitFor: "Open positions", wait: 5000 });
  const rulesRes = await p.request.get(`${BASE}/portfolio/squareoff-rules`, { failOnStatusCode: false });
  const rules = rulesRes.ok() ? ((await rulesRes.json()).rules ?? []) : [];
  const armed = rules.filter((r) => r.status === "armed").length;
  for (let i = armed; i < 2; i += 1) {
    await p.getByRole("button", { name: "+ Set Profit Booking / Stop Loss", exact: true }).filter({ visible: true }).first().click();
    await dialog(p).getByText("Set P&L Profit Booking / Stop Loss").waitFor();
    await fillRule(dialog(p));
    await dialog(p).getByRole("button", { name: "Arm rule" }).click();
    await p.waitForTimeout(2500);
    log("  armed a rule");
  }
  // One bot backtest, so the Bots page's Activity table has a row (mock mode replays stored data).
  const runs = await p.request.get(`${BASE}/bots/runs`, { failOnStatusCode: false });
  const hasBacktest = runs.ok() && JSON.stringify(await runs.json()).includes("backtest");
  if (!hasBacktest) {
    await go(p, "/bots", { waitFor: "Long Scalper", wait: 1500 });
    await p.locator("section.app-card:has(h2:text-is('Long Scalper'))").getByTitle("Backtest").click();
    await dialog(p).getByRole("button", { name: "Run backtest" }).click();
    await p.waitForTimeout(20_000);
    log("  ran a Long Scalper backtest");
  }
  await ctx.close();
}

async function main_() {
  const browser = await chromium.launch();
  try {
    if (doSetup) await setup(browser);
    for (const theme of themes) {
      log(`theme: ${theme}`);
      for (const shot of SHOTS) {
        if (only && !only.includes(shot.name)) continue;
        const ctx = await newContext(browser, theme, shot.viewport);
        const page = await ctx.newPage();
        try {
          if (shot.auth) await login(page);
          await shot.run(page, theme);
        } catch (e) {
          log(`  FAILED ${shot.name}: ${e.message.split("\n")[0]}`);
          process.exitCode = 1;
        } finally {
          await ctx.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
}

await main_();

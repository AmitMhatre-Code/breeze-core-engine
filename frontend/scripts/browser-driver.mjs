// REPL driver for Breeze Modern (frontend at http://127.0.0.1:3000, ./dev.sh topology).
// Launches headless Chromium via Playwright and exposes line commands over stdin.
// Designed for agents: pipe a heredoc script, or wrap in tmux + send-keys for iteration.
import { chromium } from "playwright";
import * as readline from "node:readline";
import * as fs from "node:fs";
import * as path from "node:path";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:3000";
const SHOT_DIR = process.env.SCREENSHOT_DIR || "/tmp/shots";
fs.mkdirSync(SHOT_DIR, { recursive: true });

let browser = null;
let context = null;
let page = null;
const consoleLog = [];

function resolveUrl(u) {
  if (!u) return BASE_URL;
  return /^https?:\/\//.test(u) ? u : new URL(u, BASE_URL).toString();
}

const COMMANDS = {
  async launch() {
    if (browser) return console.log("already launched");
    browser = await chromium.launch({ args: ["--no-sandbox"] });
    // Wider than Playwright's 1280 default — this app's desktop tables assume
    // real desktop width above the `xl` breakpoint and get clipped/scrollable at 1280.
    context = await browser.newContext({ viewport: { width: 1800, height: 1100 } });
    page = await context.newPage();
    page.on("console", (m) => consoleLog.push(`[${m.type()}] ${m.text()}`));
    page.on("pageerror", (e) => consoleLog.push(`[pageerror] ${e.message}`));
    console.log("launched. base url:", BASE_URL);
  },

  async nav(u) {
    if (!page) return console.log("ERROR: launch first");
    const url = resolveUrl(u);
    const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    console.log("nav", url, "->", resp?.status());
  },

  async "wait-for"(sel) {
    if (!page) return console.log("ERROR: launch first");
    try {
      if (sel.startsWith("text=")) {
        await page.getByText(sel.slice(5)).first().waitFor({ timeout: 15_000 });
      } else {
        await page.waitForSelector(sel, { timeout: 15_000 });
      }
      console.log("found:", sel);
    } catch {
      console.log("TIMEOUT:", sel);
    }
  },

  async click(sel) {
    if (!page) return console.log("ERROR: launch first");
    try {
      if (sel.startsWith("text=")) {
        await page.getByText(sel.slice(5)).first().click({ timeout: 10_000 });
      } else {
        await page.click(sel, { timeout: 10_000 });
      }
      console.log("click", sel, "-> OK");
    } catch (e) {
      console.log("click", sel, "-> ERROR:", e.message.split("\n")[0]);
    }
  },

  async "click-text"(text) {
    if (!page) return console.log("ERROR: launch first");
    try {
      await page.getByText(text, { exact: false }).first().click({ timeout: 10_000 });
      console.log("click-text", JSON.stringify(text), "-> OK");
    } catch (e) {
      console.log("click-text", JSON.stringify(text), "-> ERROR:", e.message.split("\n")[0]);
    }
  },

  async fill(args) {
    if (!page) return console.log("ERROR: launch first");
    // Last-space split, not first — see the `attr` command for why. Values with
    // spaces (free text) aren't supported by this convention; use `eval` +
    // proper React input events for those (see Gotchas in SKILL.md).
    const sp = args.lastIndexOf(" ");
    const sel = sp === -1 ? args : args.slice(0, sp);
    const value = sp === -1 ? "" : args.slice(sp + 1);
    try {
      await page.fill(sel, value, { timeout: 10_000 });
      console.log("fill", sel, "-> OK");
    } catch (e) {
      console.log("fill", sel, "-> ERROR:", e.message.split("\n")[0]);
    }
  },

  async type(text) {
    if (page) await page.keyboard.type(text, { delay: 20 });
  },
  async press(key) {
    if (page) await page.keyboard.press(key);
  },

  async screenshot(name) {
    if (!page) return console.log("ERROR: launch first");
    const f = path.join(SHOT_DIR, (name || `ss-${Date.now()}`) + ".png");
    await page.screenshot({ path: f, fullPage: true });
    console.log("screenshot:", f);
  },

  async eval(expr) {
    if (!page) return console.log("ERROR: launch first");
    try {
      console.log(JSON.stringify(await page.evaluate(expr)));
    } catch (e) {
      console.log("ERROR:", e.message);
    }
  },

  async text(sel) {
    if (!page) return console.log("ERROR: launch first");
    console.log(
      await page.evaluate(
        (s) => (s ? document.querySelector(s) : document.body)?.innerText ?? "(null)",
        sel || null,
      ),
    );
  },

  async cookies() {
    if (!context) return console.log("ERROR: launch first");
    console.log(JSON.stringify(await context.cookies(), null, 2));
  },

  // Intercept a GET endpoint and return a fixed JSON body — e.g. to test past a
  // license-gated UI state without a real license:
  //   mock-route /deployment/license-status {"deployment_license_status":"active","deployment_license_read_only":false}
  // Persists for the rest of this `launch` session (route un-intercepted on quit/relaunch).
  async "mock-route"(args) {
    if (!context) return console.log("ERROR: launch first");
    const sp = args.indexOf(" ");
    if (sp === -1) return console.log("usage: mock-route <path> <json-body>");
    const routePath = args.slice(0, sp);
    const body = args.slice(sp + 1);
    await context.route(`**${routePath}`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body }),
    );
    console.log("mock-route ->", routePath, "will now return:", body);
  },

  // Move the real mouse to a fractional point inside an element's box (0,0 = top-left,
  // 1,1 = bottom-right) — for testing hover/crosshair interactions that need a precise
  // pointer position, which plain `click`/`click-text` can't target.
  async "hover-at"(args) {
    if (!page) return console.log("ERROR: launch first");
    const parts = args.split(" ");
    const fracY = Number(parts.pop());
    const fracX = Number(parts.pop());
    const sel = parts.join(" ");
    try {
      const box = await page.locator(sel).first().boundingBox();
      if (!box) return console.log("hover-at", sel, "-> ERROR: no bounding box");
      await page.mouse.move(box.x + box.width * fracX, box.y + box.height * fracY);
      console.log("hover-at", sel, fracX, fracY, "-> OK", JSON.stringify(box));
    } catch (e) {
      console.log("hover-at", sel, "-> ERROR:", e.message.split("\n")[0]);
    }
  },

  async viewport(args) {
    if (!page) return console.log("ERROR: launch first");
    const [w, h] = args.split(" ").map(Number);
    if (!w || !h) return console.log("usage: viewport <width> <height>");
    await page.setViewportSize({ width: w, height: h });
    console.log("viewport ->", w, "x", h);
  },

  // Count elements matching a selector — e.g. asserting a button is truly absent
  // (0) vs just off-screen, or how many rows/checkboxes are present.
  async count(sel) {
    if (!page) return console.log("ERROR: launch first");
    console.log(await page.locator(sel).count());
  },

  // Read a DOM property (not an HTML attribute) off the first match — e.g.
  // `attr input[type=checkbox] checked` or `attr button disabled`.
  async attr(args) {
    if (!page) return console.log("ERROR: launch first");
    // Split on the LAST space, not the first — CSS selectors routinely contain
    // spaces themselves (descendant combinators), but the trailing DOM property
    // name (checked/disabled/value/...) never does.
    const sp = args.lastIndexOf(" ");
    const sel = sp === -1 ? args : args.slice(0, sp);
    const prop = sp === -1 ? "" : args.slice(sp + 1);
    try {
      console.log(await page.locator(sel).first().evaluate((el, p) => el[p], prop));
    } catch (e) {
      console.log("ERROR:", e.message);
    }
  },

  async console(filter) {
    const lines = filter === "--errors" ? consoleLog.filter((l) => /error/i.test(l)) : consoleLog;
    console.log(lines.length ? lines.join("\n") : "(no console output captured)");
  },

  // --- App-specific: mock-broker direct login (see SKILL.md "Auth"). ---
  // Registers the account if it doesn't exist yet (ignores 409 Conflict), then logs in.
  // Only works when the backend is started with ICICI_BROKER_MODE=mock (dev.sh's
  // MOCK_MARKET_MODE=LIVE/OFF_MARKET sets this automatically) — direct-login sets
  // auth cookies immediately in that mode instead of redirecting to real ICICI OAuth.
  async "login-mock"(args) {
    if (!page) return console.log("ERROR: launch first");
    const [userId, password] = args.split(" ");
    if (!userId || !password) return console.log("usage: login-mock <user_id> <password>");
    const reg = await page.request.post(resolveUrl("/api/register/direct"), {
      data: { user_id: userId, password, api_key: "test-key", secret_fragment: "test-secret" },
      failOnStatusCode: false,
    });
    if (reg.status() !== 200 && reg.status() !== 409) {
      console.log("register ->", reg.status(), await reg.text());
      return;
    }
    const login = await page.request.post(resolveUrl("/auth/direct-login"), {
      data: { user_id: userId, password },
      failOnStatusCode: false,
    });
    console.log("login ->", login.status(), await login.text());
  },

  async quit() {
    if (browser) await browser.close().catch(() => {});
    browser = null;
    context = null;
    page = null;
  },
  help() {
    console.log("commands:", Object.keys(COMMANDS).join(", "));
  },
};

const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: "driver> " });

// readline's 'line' event does NOT await async listeners — without this queue, a
// piped heredoc of commands fires them all concurrently (e.g. `nav` racing ahead of
// `launch` before Chromium has even started) and 'close' can exit the process while
// the first command is still in flight. Chaining onto `queue` serializes execution.
let queue = Promise.resolve();

rl.on("line", (line) => {
  queue = queue.then(async () => {
    const sp = line.trim().indexOf(" ");
    const cmd = sp === -1 ? line.trim() : line.trim().slice(0, sp);
    const rest = sp === -1 ? "" : line.trim().slice(sp + 1);
    if (!cmd) return;
    const fn = COMMANDS[cmd];
    if (!fn) {
      console.log("unknown:", cmd, "— try: help");
      return;
    }
    try {
      await fn(rest);
    } catch (e) {
      console.log("ERROR:", e.message);
    }
    if (cmd === "quit") process.exit(0);
    // Piped (non-interactive) input auto-closes readline as soon as EOF is read,
    // which happens well before these queued async commands finish running —
    // prompting after that throws ERR_USE_AFTER_CLOSE. Harmless to skip: there's
    // no interactive terminal to show the prompt to anyway in that case.
    if (!rl.closed) rl.prompt();
  });
});
rl.on("close", () => {
  queue = queue.then(async () => {
    await COMMANDS.quit();
    process.exit(0);
  });
});

console.log("breeze-core-engine driver —", BASE_URL, "— \"help\" for commands, \"launch\" to start");
rl.prompt();

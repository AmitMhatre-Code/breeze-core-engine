# guide/ — all Breeze Modern documentation

Two folders, two audiences, one rule: **only `guide/user/` ever leaves the repository.**

| Folder | Audience | Published? |
|---|---|---|
| [`user/`](./user/) | Traders using the app | **Yes**: rendered inside the app at `/guide`, public (no sign-in), and built into every image. |
| [`technical/`](./technical/) | The development team | **Never.** Repo only. Excluded from every Docker build context by `.dockerignore`; the publish workflow fails if any of its files turn up in the image. It documents the license-bypass env var and the DRM design. |

Planning documents, specs, the UAT plan, legal reference copies and the copyright package live in [`docs/`](../docs/README.md).

## The user guide (`user/`)

- **`guide.json`** is the manifest: parts, sections (slug, title, one-line summary) and the "last reviewed" date. Section order in the app follows it.
- **`index.md`** is the introduction on `/guide`; **`<slug>.md`** is each section, starting with `# <title>` matching the manifest.
- **`images/dark/` and `images/light/`** hold every screenshot twice. Markdown references the dark copy (`![alt](images/dark/name.png)`); the app swaps in the light one for light-theme readers.
- Write links as `other-section.md#heading-anchor` (GitHub anchor rules) so they work on GitHub and in the app. Callouts use GitHub syntax (`> [!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`).
- Each of the in-app Help topics (`frontend/src/lib/help/topics.ts`) has a `guide` field pointing at its section here.

**How it gets into the app:** `frontend/src/lib/guide/content.ts` reads `guide/user/` at build time and the `/guide` pages are statically generated. `frontend/scripts/sync-guide-assets.mjs` (run by `predev`/`prebuild`) copies the images into `frontend/public/guide-assets/` (git-ignored). The root `Dockerfile` copies `guide/user` to `/guide/user` in the frontend build stage; `docker-compose.yml` passes it as the `guide` build context.

**Checks:** `npm test` in `frontend/` runs `src/lib/guide/content.test.ts`, which fails if a manifest section has no file (or a file has no manifest entry), a heading anchor is duplicated, a link or anchor is broken, a screenshot is missing in either theme, a Help topic points at a missing section, or the loader could reach outside `guide/user`.

**Screenshots:** start the app in mock mode (`MOCK_MARKET_MODE=LIVE ./dev.sh`), then from the repo root:

```bash
node frontend/scripts/capture-guide-screenshots.mjs --setup          # first run: creates parked orders, armed rules, a bot backtest
node frontend/scripts/capture-guide-screenshots.mjs                  # every shot, both themes
node frontend/scripts/capture-guide-screenshots.mjs --only portfolio --theme dark
```

**Keeping it current:** when a UI change adds, removes or renames a control, update the matching section and re-capture its screenshots (see CLAUDE.md). Bump `reviewed` in `guide.json` after a full review.

| Part | Sections |
|---|---|
| Getting started | [Welcome](./user/welcome.md), [Before you begin](./user/before-you-begin.md), [Registering](./user/registering.md), [Signing in](./user/signing-in.md), [Passwords and credentials](./user/account-recovery.md), [Finding your way around](./user/finding-your-way.md) |
| The pages | [Dashboard](./user/dashboard.md), [Portfolio](./user/portfolio.md), [Performance](./user/performance.md), [Order Book](./user/order-book.md), [Place Order](./user/place-order.md), [Basket Order](./user/basket-order.md), [Strategy Builder](./user/strategy-builder.md), [Signals](./user/signals.md), [Bots](./user/bots.md), Settings: [trading setup](./user/settings-trading.md), [automation and alerts](./user/settings-automation.md), [diagnostics](./user/settings-diagnostics.md), [danger zone](./user/settings-danger-zone.md) |
| Topics | [Margins](./user/margins.md), [Backtests](./user/backtests.md), [ICICI API limits](./user/api-limits.md), [Read-only mode](./user/read-only-mode.md), [Troubleshooting](./user/troubleshooting.md), [Glossary](./user/glossary.md) |

## Technical documentation (`technical/`)

| Document | What it covers |
|----------|----------------|
| [Technical overview](./technical/technical-overview.md) | **Simplified**, customer-readable description of the architecture, data flows and what leaves the server. The dev team sends it to customers who ask; the user guide's FAQ offers it on request. Keep it free of hostnames, env vars and internals. |
| [Functionality](./technical/functionality.md) | Feature areas, screens, APIs used by the UI. |
| [Architecture](./technical/architecture.md) | Stack, runtime topology, components, data stores, integration boundaries. |
| [Design decisions](./technical/design-decisions.md) | Rationale for major choices — read before "fixing" something that looks odd. |
| [User and system flows](./technical/flows.md) | Sequence and flow diagrams (login, broker auth, registration, trading data, settings, deployment). |
| [Configuration reference](./technical/configuration-reference.md) | Every environment variable and its default. |
| [AWS deployment](./technical/aws-deployment.md) | Current (portal CloudFormation) vs dormant legacy deploy paths, GHCR publishing, the cross-repo DRM key contract. |
| [Backtest findings](./technical/backtest-findings.md) | What the signal and bot backtests have found. Internal; the user guide deliberately carries no findings. |
| [License management](../../breeze-saas-portal/docs/license-management.md) | Deployment licensing — authoritative doc in **breeze-saas-portal**. |

The `legacy/` directory in the repo is a **read-only** historical snapshot; it is not described as part of the running system here.

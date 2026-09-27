# docs/ — plans, specs and filings

User and technical documentation lives in **[`guide/`](../guide/README.md)**, not here:

- **[`guide/user/`](../guide/user/)** is the user guide, published inside the app at `/guide`.
- **[`guide/technical/`](../guide/technical/)** holds the engineering docs (architecture, design decisions, flows, functionality, configuration, AWS deployment) and the simplified technical overview. It stays in the repository and is never published.

This folder keeps working material that is neither:

| Document | What it is |
|----------|------------|
| [Bots MVP plan](./bots-mvp-plan.md), [Scalping bots plan](./bots-scalping-plan.md), [CAS Bingo plan](./bots-cas-bingo-plan.md) | Bot design and build plans (read §9 of the MVP plan first — it reverses §3/§4). |
| [Signals streamline plan](./signals-streamline-plan.md) | Plan behind the current Signals page and the 30-day backtest gate. |
| [PB/SL reliability and API budget plan](./pbsl-reliability-and-api-budget-plan.md), [Strategy group PB/SL plan](./strategy-group-pbsl-plan.md) | Profit Booking / Stop Loss plans. |
| [Strategy Builder portfolio margin plan](./strategy-builder-portfolio-margin-plan.md), [Options strategies spec](./options-strategies.md), options strategy engine specs ([Gemini](./options_strategy_engine_spec%20-%20Gemini.md), [OpenAI](./options_strategy_engine_spec%20-%20OpenAI.md)) | Strategy Builder specs and plans. |
| [UAT plan](./uat-plan.md) | Manual post-release smoke test. |
| [Terms and conditions](./breeze_terms_and_conditions.md), [Login terms](./login-terms.md) | Reference copies of legal text. The live text is served by breeze-saas-portal. |

## Copyright submission package

| Document | Purpose |
|----------|---------|
| [Copyright submission guide](./copyright-submission.md) | Scope, originality statement template, and filing checklist. |
| [Third-party exclusions](./third-party-exclusions.md) | Explicit denylist of external/common/generated/sensitive artifacts to omit from the filing. |
| [Single code document](./code-submission.md) | Consolidated first-party code document (PDF-ready markdown). |

The code document intentionally excludes third-party dependencies (`node_modules`, `.venv`), generated/build outputs, runtime logs, secrets (`.env*`), local databases, and the read-only `legacy/` snapshot.

# Strategy Builder — portfolio-aware (incremental) margin

Implementation spec. Status: **shipped.** All phases (1, 2, 3, 5.1, 5.2, 6) implemented and
tested; `docs/design-decisions.md` #23 records the summarized decision. See the progress log
below for what each phase actually did (including several corrections to this original spec,
made during implementation and called out explicitly where they matter) and the one open
production-validation gate (§12) that still needs a real ICICI session before full release.

## Progress log

- **Phase 1 — done.** `backend/src/icici_breeze_backend/app/services/portfolio_margin_netting.py`
  (`normalize_stock`, `PositionSet`, `positions_for_underlying`, `positions_to_margin_input`,
  `existing_span`). `route_hedge.py`'s duplicate `_normalize_stock` removed in favour of the
  shared one. Tests: `backend/tests/test_portfolio_margin_netting.py` (11 passed).
- **Phase 2 — done.** `processor.strategy_builder_margin` accepts `existing_legs` /
  `existing_span_value` / `netting_position_count` / `netting_unavailable_reason`, default
  `None` ⇒ byte-identical to pre-netting behaviour (verified — full existing suite green).
  Netting implemented on both the live `breeze_api` tail path (2 `margin_calculator` calls:
  standalone unchanged + a new combined call, best-effort — any failure of the second call
  leaves the response as standalone-only) and the fully-offline `exchange_baseline` path
  (same-expiry-only per D6, via two extra local `_portfolio_baseline_span_margin` calls, zero
  API spend). Tests: `backend/tests/test_strategy_builder_margin_netting.py` (10 passed).
  **Correction made during implementation, not in the original spec text:** the new
  "benefit from netting against open positions" field is named `positions_margin_benefit`,
  **not** `margin_benefit` — that name is already taken in this same `Success` payload (and
  in the frontend's `OnDemandMarginData`/`StrategyLegsPanel`/`BasketLegsPanel`) by a
  pre-existing, different quantity: the *intra-structure* netting benefit (this candidate's
  own legs netted against each other). All later sections of this doc already reflect the
  corrected name — see the note under §6.3's field table for the full explanation, and §9.1
  for the related fix to how the frontend's existing `marginBenefit` must be recomputed
  (`sumStandalone − standaloneSpan`, not `sumStandalone − basket.span`) once the basket call
  can return an incremental figure.
- **Phase 5.1 — done.** Backend: `StrategyBuilderMarginRequest.net_against_positions`
  (default `True`) added; `route_strategy_builder.post_margin` resolves the `PositionSet`
  and, for `breeze_api` only (never for `exchange_baseline` — that would spend live API
  budget the baseline source exists to avoid), the live `M(P)`, then threads them into
  `strategy_builder_margin`. Frontend (`real-margin.ts`): `fetchRealMarginWithElm` now also
  parses the netting fields via a new `parsePositionsNettingFromResponse`
  (`leg-ui-helpers.ts`); `OnDemandMarginData` widened with `standaloneSpan`,
  `positionsMarginBenefit`, `nettedAgainstPositions`, `nettedPositionCount`,
  `nettingUnavailableReason`; the pre-existing intra-structure `marginBenefit` is now
  computed as `sumStandalone − standaloneSpan` (was `sumStandalone − basket.span`, which
  would have silently gone wrong the moment the basket span became incremental — caught and
  fixed during this phase, see the real-margin.test.ts case for it);
  `prefillSpanMargin(spanMargin, forKey)` widened to `prefillMargin(data, forKey)` per the
  original §9.1 suggestion, one call site updated in `strategy-builder/page.tsx`. Tests:
  `backend/tests/test_route_strategy_builder_margin_netting.py` (5 passed),
  `frontend/src/lib/strategy-builder/real-margin.test.ts` (4 passed). Full backend suite
  (1291 passed), full frontend suite (197 passed), `tsc --noEmit` clean, lint clean on all
  touched files (pre-existing, unrelated `react-hooks/set-state-in-effect` errors remain
  elsewhere in the repo — verified via `git diff` that none are in files this phase touched).
- **Phase 3 — done.** This is the phase where the plan text and the actual implementation
  diverge most, in ways worth reading before touching this code again:
  - **No cache-key fingerprinting needed.** §7.2's premise ("margin_key/structural_margin_key
    must include the positions fingerprint or a stale netted span leaks across builds") turned
    out to be wrong: `EngineContext` (and therefore `ctx.unit_span_by_structure`) is
    constructed fresh per `run_propose_trades` call, never reused across builds — verified by
    reading the construction site (`orchestrator.py`, `ctx = EngineContext(...)` inside
    `run_propose_trades`). `margin_key`/`structural_margin_key` were **not** touched. The real
    risk was different: a netted-incremental one-lot value and a standalone one-lot value for
    the *same* structural key are different numbers that must never share a dict slot within
    one build. Fixed with a second, separate cache: `EngineContext.unit_incremental_by_structure`
    (`types.py`), alongside the existing `unit_span_by_structure`.
  - **Sizing calls `strategy_builder_margin` directly, not a bespoke incremental formula.**
    Because Phase 2 already made `span_margin_required` mean "incremental when netted,
    standalone otherwise", every probe the secant needs (one-lot netted, the anchor point, the
    final full-quantity fetch) is just another `MarginFetchRequest`/`strategy_builder_margin`
    call with `existing_legs`/`existing_span_value` set — no separate netting math lives in the
    engine. This also means the exchange_baseline-vs-breeze_api split from §6.4 needed **no
    engine-side duplication**: `EngineContext.existing_span` is populated (via a live
    `_netted_span_for_legs` call) only for `margin_source=breeze_api`; for `exchange_baseline`
    it stays `None` and `strategy_builder_margin`'s own baseline branch nets M(P) itself,
    offline, same-expiry-only (D6) — the engine just passes `netting_legs` through unfiltered
    and lets that branch do the filtering + warnings it already had from Phase 2.
  - **Batching, not one-off calls.** §7.4's pseudocode reads as a per-structure sequential
    secant; the actual implementation batches every anchor probe across ALL structures needing
    one into a single `fetch_margins_concurrent` round (`budget_resize.py`'s `anchor_requests`
    list), mirroring the pre-existing standalone-probe batching. Structures with no meaningful
    overlap with the book (`|incr1 − standalone1| ≤ 2%`) never enter the secant at all.
  - **Failure/success ambiguity fixed at the source.** `margin_async_fetch.py`'s
    `_fetch_one_margin` used to return a bare `0.0` on both "true API failure" and (previously
    impossible) "legitimate non-positive span". Once incremental margin can legitimately be
    `≤ 0` (D8), that ambiguity becomes a real over-leverage risk: a failed netted probe could
    silently read as "fully offset the position". Fixed by having it return `(key, span, ok)`
    instead of `(key, span)`, and `fetch_margins_concurrent` gained an optional `failed_keys`
    out-param — existing callers are unaffected (the public `dict[tuple, float]` return is
    unchanged), new netted call sites use `failed_keys` to keep a failed probe **absent** from
    the cache rather than silently `0.0`.
  - **Shrink check** (§7.5) implemented as `orchestrator._shrink_and_refetch`: the secant is a
    two-point approximation, so the FINAL full-quantity netted fetch in
    `attach_margins_and_returns` is authoritative. If `incremental + ELM(N)` exceeds budget at
    the secant-chosen `N`, shrink once (`N' = floor(N · budget / total)`), re-fetch that one
    structure, and skip the result only if even one lot doesn't fit. Exactly one re-fetch, not
    a loop — verified by a test asserting the mock margin function was called exactly twice.
  - **D8 ranking** implemented as a stable sort of `recommended_results`/`relaxed_results` by
    `(not margin_released, -net_premium)` right after `attach_margins_and_returns`, plus a
    `"Margin releasing"` badge — not a change to `ranking.py`'s `engine_score`, which is
    computed earlier (during strategy generation, before margin is known) and is a *per-strategy*
    variant-selection score, not the cross-strategy card order.
  - API contract: `StrategyResult` gained `standalone_span_margin`, `positions_margin_benefit`
    (**not** `margin_benefit` — same collision as Phase 2, see §6.3), `netted_against_positions`,
    `margin_released`; mirrored into `ProposedTradeOut` (`app/domain/options_strategy.py`) and
    `_result_to_trade_dict`. `ProposeTradesSuccess` gained `netting_unavailable_reason` (D7),
    populated from `ctx.netting_unavailable_reason` in the final `success_payload`.
  - Tests: `backend/tests/test_engine_portfolio_margin_netting.py` (14 passed) — secant solver
    unit tests, `resize_results_to_budgets` with netting active/inactive/short-circuited/
    positions-unavailable, and `attach_margins_and_returns` covering negative incremental,
    positive incremental, the shrink check, shrink-to-one-lot-still-over-budget, and a failed
    final netted fetch never surfacing an unverified figure. Full backend suite: 1310 passed,
    2 skipped (up from 1296 before this phase — no regressions).
  - **Known gap, deliberately deferred to Phase 6:** per-structure baseline warnings
    (`positions_not_netted_other_expiry`) computed inside `strategy_builder_margin` are
    discarded by the engine's margin-fetch path (`_span_from_response` only extracts the span
    figure). Build-Your-Own already surfaces these; the propose-trades engine does not yet.
- **Phase 5.2 — done.** Scoped down to `margin_source=breeze_api` only: the Exchange Risk
  Baseline branch inside `_resolve_leg_margin_with_source` (`processor.py`) uses a flat
  per-contract lookup (`resolve_exchange_baseline_margin`), not the portfolio risk-array scan
  `strategy_builder_margin` uses elsewhere — netting it properly would mean duplicating that
  scan machinery for a single-leg-at-a-time function, which this phase did not take on. Left
  standalone-only for baseline source (never worse than today); documented explicitly in the
  function's docstring so it isn't mistaken for an oversight later.
  `_resolve_leg_margin_with_source` gained the same `existing_legs`/`existing_span_value`/
  `netting_position_count` kwargs as Phase 2, doing its own standalone+combined two-call
  pattern directly against `breeze.margin_calculator` (this function predates
  `strategy_builder_margin` in this call chain and doesn't route through it — replacing it
  wholesale was considered and rejected: `strategy_builder_margin`'s baseline branch computes
  a *different* number for a single leg than this function's flat lookup, so swapping it in
  would have silently changed the standalone figure this scan has always returned). Threaded
  through `get_options` (both new kwargs) and resolved once in `uncovered_shorts` (positions +
  live M(P), not per candidate strike) before the CE and PE scan calls — covers both
  `route_uncovered_shorts.py` and, via `run_covered_shorts_scan` → `breeze.uncovered_shorts`,
  the covered-shorts scan and its Strategy Builder variant, all in one place. Tests:
  `backend/tests/test_shorts_scan_margin_netting.py` (9 passed, zero prior coverage existed on
  this surface). Full backend suite: 1319 passed, 2 skipped.
- **Phase 6 — done.** `map-proposed-legs.ts` needed no change (it only maps per-leg fields;
  the netting fields are trade-level, not leg-level — the original progress-log note above
  over-anticipated this). `types.ts`: `ProposedTrade` gained the four new fields (named per the
  §6.3 collision note — `positions_margin_benefit`, not `margin_benefit`), `ProposeTradesSuccess`
  gained `netting_unavailable_reason`. `ProposedStrategyTradeCard.tsx`: a "Margin releasing"
  badge next to the strategy name (`marginReleased = status==="ok" && margin_released===true`);
  the Margin stat shows `+₹X` in the up/gain tone when released, incremental as before
  otherwise; a caption line under the stats grid shows either the premium-impact framing for a
  released card (D8's "not free money" point — released margin is still paid for in premium)
  or "₹X less than standalone — netted against your open positions" when
  `netted_against_positions && positions_margin_benefit > 0`. `page.tsx`: `applySelectedTrade`
  now passes `standaloneSpan`/`positionsMarginBenefit`/`nettedAgainstPositions` into
  `prefillMargin` (not just `spanMargin`, closing the gap `prefillMargin`'s Phase 5.1 widening
  anticipated); a `netting_unavailable_reason` banner added next to the existing `generateError`
  banner. `tsc --noEmit` clean, full frontend suite 197 passed (no new unit tests added — no
  jsdom/testing-library in this repo's vitest config, confirmed by grep; all existing frontend
  tests are `lib/`-only pure-function tests, zero `.test.tsx` files anywhere, so a component
  test here would introduce a new testing paradigm rather than follow an established one).
  **Live-verified instead**, per this session's UI-change obligation: ran `./dev.sh` in
  `MOCK_MARKET_MODE=OFF_MARKET`, drove Strategy Builder end-to-end via the `run` skill's
  Playwright REPL, generated an Income-strategies proposal for NIFTY. The mock broker's fixture
  positions (`dev/fixtures/responses.py`'s `mock_portfolio_position_rows()`, which happens to
  hold NIFTY shorts) triggered real netting: multiple cards rendered
  "₹4.50 Lac less than standalone — netted against your open positions." (and similar, up to
  "₹17.47 Lac less than standalone" at a larger margin budget) with correct money formatting;
  selecting a netted card correctly prefilled the Build-Your-Own leg panel's Net SPAN Margin.
  Zero console errors traceable to the new fields (only the pre-existing, documented-harmless
  `/api/login-disclosure/current` 404 and an unrelated pre-existing hydration warning on
  `ExpirySelectPill`). Did **not** manage to naturally trigger a `margin_released` (negative
  incremental) card in mock mode — the mock `margin_calculator` floors every individual call's
  span at 0, and constructing a specific mock scenario where the *difference* of two floored
  calls goes negative wasn't attempted given time already spent; that display path relies on
  the backend unit tests (`test_engine_portfolio_margin_netting.py`) plus a careful code read,
  not a live screenshot. **Not built:** a `positionsMarginBenefit` stat tile inside
  `StrategyLegsPanel.tsx`/`BasketLegsPanel.tsx` for the Build-Your-Own leg panel itself — the
  live check showed `prefillMargin` correctly carries the data into the hook, but no UI in that
  panel currently reads `positionsMarginBenefit`, only the pre-existing intra-structure
  `marginBenefit`. Small, contained follow-up if wanted; out of this phase's original scope
  (§10.2 was written against `ProposedStrategyTradeCard` specifically).
- **Not started:** design-decisions.md entry (this is the last remaining item in the plan).

---

## 1. Problem

When a user runs Strategy Builder for a scrip, every margin figure is computed as if the
user held **no positions at all**. Two consequences:

1. **Displayed margin is wrong** — it shows the standalone SPAN of the proposed structure,
   not what the trade will actually cost against an account that already holds offsetting
   risk in the same underlying.
2. **Lot counts are wrong** — sizing divides the user's margin budget by that same
   standalone number, so the builder under-sizes every structure that would benefit from
   the user's existing book.

SPAN is a portfolio risk model. A proposed bear call spread against an existing naked short
call can cost a fraction of its standalone margin, or even *release* margin.

---

## 2. Decisions (locked — do not relitigate)

| # | Decision | Rationale |
|---|---|---|
| D1 | Netting universe = **same scrip, all open expiries, one exchange** | SPAN nets across expiries within an underlying. Matches the existing `_compute_netted_margins` grouping. |
| D2 | Displayed margin = **incremental**, plus a secondary line showing the actual ₹ benefit | Benefit is computable with zero extra API calls (see §4), so show the number rather than a vague note. |
| D3 | Sizing uses **incremental** margin | The user sizes against capital they can actually deploy. |
| D4 | Scope = **Build-Your-Own + Propose-Trades engine + covered/uncovered shorts scan** | All three surfaces show margin figures. |
| D5 | Nonlinearity handled by **two-point secant + shrink check** | Incremental margin saturates; naive division over-sizes catastrophically. |
| D6 | `exchange_baseline` source nets **same-expiry only**, warns about the rest | Its risk-array sheet is keyed per stock+expiry and has no inter-month spread logic. Overstates margin; never understates. |
| D7 | Positions fetch failure ⇒ **fall back to standalone + visible warning** | Never size against an offset that could not be verified. |
| D8 | Return-on-margin ranking uses **incremental, unfloored** | Capital efficiency is the honest metric. Negative case handled per §7.4. |
| D9 | **Futures out of scope** for this release | App is options-only today. Revisit later. |
| D10 | **No NSE↔BSE cross-netting** | Matches existing `_compute_netted_margins` rationale. |

### Non-goals

- Do not net across underlyings.
- Do not net futures or equity holdings.
- Do not change the meaning of the user's margin-budget input (it stays "capital I consider free").
- Do not touch `legacy/`.

---

## 3. Reuse inventory — READ THIS BEFORE WRITING CODE

Most of the netting machinery already exists and ships in production. **Do not rebuild it.**

| What | Where | Notes |
|---|---|---|
| Netted multi-leg SPAN for a set of open legs on one exchange | `processor._netted_span_for_legs()` — `backend/src/icici_breeze_backend/app/services/processor.py:2139` | Sends every leg (buys included) in ONE `margin_calculator` call so ICICI nets. Returns `None` on any failure. **This is `M(P)`.** |
| Date- and composition-keyed same-day cache for the above | `_portfolio_netted_cache_key()`, `_cached_span_margin_required` / `_remember_span_margin_required`, TTL `_portfolio_margin_cache_ttl_seconds()` (until IST midnight) in `processor.py` | Key encodes the IST date and exact leg composition, so it busts on any qty/contract change and at the day boundary (design-decisions #37; it was a rolling 24h before 2026-09-17). Already warm whenever the Portfolio page has loaded that day. |
| Per-underlying grouping across expiries | `processor._compute_netted_margins()` — `processor.py:2193` (see the `by_underlying` block at `:2242`) | Already implements exactly the D1 universe, with a comment explaining why netting stops at one underlying. |
| Open positions fetch (options-only, NFO/BFO) | `processor.get_positions()` — `processor.py:1924`; filter at `:1965` | Already excludes futures per D9. Cached via `_cached_live_snapshot` (15s TTL). |
| Same-scrip position filter | `_normalize_stock()` / `_filter_bucket_positions()` — `backend/src/icici_breeze_backend/app/api/v1/route_hedge.py:43` / `:47` | Move to a shared module (§5.1); hedge route then imports from there. |
| Exchange-baseline portfolio SPAN (scanning risk − NOV) | `span_portfolio_scan.compute_portfolio_span_margin()` — `.../services/reference_data/span_portfolio_scan.py:146` | Already computes `margin_benefit`. Bails on mixed expiries by design. |
| Concurrent margin fetch | `margin_async_fetch.fetch_margins_concurrent()` — `.../options_strategy_engine/margin_async_fetch.py:104` | `MarginFetchRequest` needs no new fields; prepend position legs into `margin_input`. |
| Mock `margin_calculator` that models hedging | `backend/src/icici_breeze_backend/dev/mock_broker.py:379` | `span = max(0, naked·net_factor − hedge)`. Long legs reduce span, so it CAN produce negative incrementals — the sizing branches are testable locally. |
| Existing netting tests | `backend/tests/test_portfolio_netted_margin.py` | `_FakeBreeze` there is a good template for new fixtures. |

---

## 4. Core formula and invariants

For candidate structure `S` at `N` lots, `P` = open option positions in the same scrip and
exchange (all expiries):

```
M(X)            = netted SPAN of leg-set X, from one margin_calculator call
incremental(N)  = M(P + S_N) − M(P)          ← displayed margin, and the sizing denominator
benefit(N)      = N · M(S₁) − incremental(N) ← displayed secondary line
```

`benefit` costs **no extra API call** because standalone SPAN is linear in lots
(`N · M(S₁)`), and `M(S₁)` is already fetched today by `budget_resize`.

### Invariants (assert these in tests)

- `incremental(N) ≥ −M(P)` — a trade can release at most all the margin the existing book
  was consuming, never more.
- `M(X) ≥ 0` for any leg-set (broker clamps; baseline path clamps at
  `span_portfolio_scan.py:189`).
- `incremental(N)` is **monotonically non-decreasing in N** for a fixed structure. Rely on
  this for the secant; if a live response violates it, log and fall back to standalone.
- With `P = ∅`: `incremental(N) == N · M(S₁)` and `benefit == 0`. Every existing test must
  still pass under this condition.

### Assumption accepted without local proof

**Standalone SPAN is exactly linear in lots.** True by construction in the baseline scan
(scanning risk and NOV both scale with quantity) and already assumed by today's
`budget // unit_span` sizing. Real SPAN has short-option minimum charges that may step.
**This must be A/B verified on the production instance before release** — per the
static-IP constraint, live `margin_calculator` cannot be exercised locally, and mock mode's
implementation is linear so it will always agree. See §10.

---

## 5. Phase 1 — Shared netting primitives

New module: `backend/src/icici_breeze_backend/app/services/portfolio_margin_netting.py`

### 5.1 `normalize_stock(raw: str) -> str`

Move verbatim from `route_hedge.py:43`. Update `route_hedge.py` to import it (do not leave
a duplicate).

### 5.2 `positions_for_underlying(...)`

```python
@dataclass(frozen=True)
class PositionSet:
    rows: list[dict]           # raw get_positions() rows, same scrip + exchange, all expiries
    fingerprint: str           # stable hash of leg composition; "" when rows is empty
    expiries: list[str]        # distinct expiry_date values present (display format)
    available: bool            # False when the positions fetch itself failed
    error: str | None

def positions_for_underlying(
    proc, user_id: str, stock_code: str, exchange_code: str
) -> PositionSet
```

- Calls `proc.get_positions(user_id)`.
- On non-200 or exception: return `PositionSet([], "", [], available=False, error=...)`.
  **Never raise.**
- Filter: `normalize_stock(row["stock_code"]) == normalize_stock(stock_code)` **and**
  `row["exchange_code"] == exchange_code` **and** `int(row["quantity"]) != 0`.
- Empty result is `available=True` with `rows=[]` — "no positions" is a valid, successful
  answer and must not trigger the D7 fallback warning.
- `fingerprint`: reuse the identity format from `_portfolio_netted_cache_key`
  (`stock:expiry:strike:right:action:quantity`, sorted, joined by `|`), then
  `hashlib.sha256(...).hexdigest()[:16]`.

### 5.3 `positions_to_margin_input(rows: list[dict]) -> list[dict]`

Convert raw position rows to `margin_calculator` leg dicts. **Each row keeps its own
`expiry_date`** — this is the whole point, and why this cannot go through
`legs_to_margin_input` (which hardcodes one expiry for all legs,
`.../options_strategy_engine/helpers.py:142`).

Copy the field shape used at `processor.py:2160` exactly, including the constant fields
(`cover_order_flow: "N"`, `fresh_order_type: "N"`, `cover_limit_rate: "0"`,
`cover_sltp_price: "0"`, `fresh_limit_rate: "0"`, `open_quantity: "0"`).

### 5.4 `existing_span(proc, breeze, user_id, exchange_code, position_set) -> float | None`

Thin wrapper over `proc._netted_span_for_legs(breeze, user_id, exchange_code, rows)`.
Returns `0.0` when `rows` is empty (no positions ⇒ no existing margin, and no API call).
Returns `None` on failure — callers treat `None` as "netting unavailable" per D7.

The same-day composition cache inside `_netted_span_for_legs` means `M(P)` is typically free.

---

## 6. Phase 2 — `strategy_builder_margin` netting

File: `processor.py:4073`.

### 6.1 New keyword arguments

```python
existing_legs: list[dict] | None = None,   # already in margin_input shape (§5.3)
existing_span_value: float | None = None,  # M(P); pass through to avoid recompute
netting_position_count: int = 0,
```

### 6.2 `breeze_api` path

At `processor.py:4281` the call is `breeze.margin_calculator(margin_input, ...)`.
Change to send `existing_legs + margin_input`. Then:

```
combined            = Success.span_margin_required   (from the netted call)
standalone          = <same structure priced without existing_legs>
incremental         = combined − existing_span_value
```

`standalone` is **not** re-fetched here. Callers that need it pass it in or derive it
linearly (§7). For the Build-Your-Own single-shot case the caller already has it (§9).

### 6.3 New `Success` fields

| Field | Meaning |
|---|---|
| `span_margin_required` | **Now the incremental figure** when netting applied. Unchanged (standalone) otherwise. |
| `standalone_span_margin` | The old number. Always present when known. |
| `existing_span_margin` | `M(P)`. |
| `combined_span_margin` | `M(P + S)`. |
| `positions_margin_benefit` | `standalone − incremental`, floored at 0 for display. **Not** `margin_benefit` — that field already exists in this same `Success` payload on the exchange-baseline path (`span_portfolio_scan.compute_portfolio_span_margin`, §3) meaning the *intra-structure* netting benefit (the candidate's own legs netted against each other), and is also what `frontend/src/lib/strategy-builder/real-margin.ts` derives client-side and `StrategyLegsPanel`/`BasketLegsPanel` already render today. Reusing the name would silently conflate two different quantities and could clobber the existing field. Keep both, under different names. |
| `netted_against_positions` | bool |
| `netted_position_count` | int |
| `netting_unavailable_reason` | string, present only under D7 fallback |

> **Semantic change, call it out in review:** `span_margin_required` flips meaning when
> netting is active. This is deliberate — every existing reader
> (`parseSpanMarginFromResponse` in `frontend/src/lib/strategy-builder/leg-ui-helpers.ts:27`,
> `_span_from_response` in `margin_async_fetch.py:52`) then picks up the correct number with
> no change. Anything wanting the old number reads `standalone_span_margin`.
> The Portfolio page's netted margin is a **different** code path
> (`_netted_span_for_legs`, not `strategy_builder_margin`) and is unaffected.

### 6.4 `exchange_baseline` path (D6)

`_portfolio_baseline_span_margin` (`processor.py:563`) returns `{"found": False}` when legs
span multiple expiries or stock codes — see the guard at `processor.py:588`.

Therefore: **filter `existing_legs` to the build expiry only** before passing them down this
path. For every position row dropped, append a warning to the existing `warnings[]` list:

```python
{
  "type": "positions_not_netted_other_expiry",
  "stock_code": ..., "expiry_date": ..., "strike_price": ..., "right": ...,
  "message": "Position in <expiry> not netted — Exchange Risk Baseline nets within one expiry only.",
}
```

ELM (`elm_legs` / `elm_addon`) is computed on the **proposed legs only**. Existing positions
already carry their own ELM in the user's account. Do not add existing legs to `elm_legs`.

---

## 7. Phase 3 — Engine sizing

### 7.1 `EngineContext` additions

File: `.../options_strategy_engine/types.py:143`.

```python
positions: Any | None = None          # PositionSet
existing_span: float | None = None    # M(P)
netting_available: bool = False
netting_warnings: list[str] = field(default_factory=list)

@property
def positions_fingerprint(self) -> str:
    return self.positions.fingerprint if self.positions else ""
```

Populate in `run_propose_trades` (`orchestrator.py:207`) during the setup phase, before any
strategy calculator runs.

### 7.2 Cache keys MUST include the positions fingerprint

Both key functions currently key on structure alone. A netted span cached under a bare
structural key will leak across builds after the user squares off a leg.

- `helpers.margin_key()` — `helpers.py:132` — append `ctx.positions_fingerprint`.
- `sizing.structural_margin_key()` — `sizing.py:105` — append `ctx.positions_fingerprint`.
  This changes the signature; update the three call sites in `budget_resize.py` (`:38`, `:78`).
- `ctx.unit_span_by_structure` is keyed by the above, so it follows automatically.

### 7.3 `StrategyResult` additions

File: `types.py:112`.

```python
standalone_span_margin: float | None = None
positions_margin_benefit: float | None = None   # NOT margin_benefit -- see §6.3
netted_against_positions: bool = False
margin_released: bool = False        # True when incremental <= 0
```

`span_margin` keeps its name and now carries incremental.

### 7.4 Sizing algorithm — `budget_resize.resize_results_to_budgets`

Replace the linear block at `budget_resize.py:110-137`.

**Inputs per structure:** `L` = lot size, `budget` = `ctx.margin_rupees`,
`M_S1` = standalone one-lot SPAN (the fetch that already happens at `budget_resize.py:38-56`
— keep it, unchanged, no position legs), `M_P` = `ctx.existing_span`,
`elm1` = one-lot ELM from `elm_addon`, `nd1` = one-lot net debit
= `max(0, −net_premium(unit_legs))`.

```
# 0. No netting available or no positions -> today's behaviour, unchanged.
if not ctx.netting_available or not ctx.positions.rows:
    <existing linear path>; return

# 1. Netted one-lot probe.
M_PS1 = fetch(M_P legs + S at 1 lot)
incr1 = M_PS1 - M_P

# 2. Short-circuit: no meaningful overlap with the book.
#    Saves the anchor call on the majority of candidates.
if abs(incr1 - M_S1) <= 0.02 * max(M_S1, 1.0):
    <existing linear path using M_S1>; return

# 3. Margin-side lot cap.
if incr1 + elm1 <= 0:
    # Margin does not bind at one lot. It WILL bind once the offset saturates,
    # so still probe, using the standalone-affordable count as the far point.
    N_probe = max(2, floor(budget / max(M_S1 + elm1, 1.0)))
    margin_lots = secant_solve(1, incr1, N_probe, fetch_incr(N_probe))
    if margin_lots is UNBOUNDED:
        margin_lots = INFINITY          # premium/max-loss will bind instead
else:
    N_opt = floor(budget / (incr1 + elm1))     # optimistic upper bracket
    if N_opt <= 1:
        margin_lots = N_opt
    else:
        margin_lots = secant_solve(1, incr1, N_opt, fetch_incr(N_opt))

# 4. Other constraints (existing behaviour).
risk_lots    = floor(max_loss_rupees / unit_max_loss)  if max_loss_rupees else INFINITY
premium_lots = floor(budget / nd1)                     if nd1 > 0 else INFINITY

lots = min(margin_lots, risk_lots, premium_lots)
if lots < 1: skip result, reason "Insufficient margin or max-loss budget for one lot."
```

`secant_solve(n1, i1, n2, i2)`:

```
slope = (i2 - i1) / (n2 - n1)
# incr(N) ~= i1 + slope*(N - n1); total(N) = incr(N) + N*elm1 <= budget
denom = slope + elm1
if denom <= 0: return UNBOUNDED
N = floor((budget - i1 + slope*n1) / denom)
return clamp(N, 1, n2)
```

Guard `slope < 0` (violates the monotonicity invariant): log a warning, fall back to the
standalone linear path for that structure.

**Rationale for the negative branch:** incremental is negative only up to the quantity that
actually offsets the existing short. Buy 6 lots against a 5-lot short and the 6th is naked
long again — the function crosses zero and the binding constraint flips from margin to
premium outlay. This mirrors the existing long-only branch at `budget_resize.py:87`, which
already treats the margin budget as a cap on premium outlay.

### 7.5 Shrink check — `attach_margins_and_returns`

File: `orchestrator.py:104`.

`margin_input` becomes `positions_to_margin_input(ctx.positions.rows) + legs_to_margin_input(...)`.

After `fetch_margins_concurrent` returns, per result:

```
combined   = span_by_key[key]
incr       = combined - M_P
standalone = N* * M_S1                      # linear; no extra call
total      = incr + elm(N*)

if total > budget:
    N' = max(1, floor(N* * budget / total))
    if N' < N*:
        rescale to N', re-fetch that one structure once, recompute incr
        if still over budget at N' == 1:
            status = "skipped"
            skip_reason = "Insufficient margin for one lot at netted SPAN."
            continue

r.span_margin              = incr          # may be <= 0
r.standalone_span_margin   = standalone
r.positions_margin_benefit = max(0, standalone - incr)
r.netted_against_positions = True
r.margin_released          = incr <= 0
```

**Do exactly one shrink round.** Not a convergence loop — the API budget cannot absorb it.

### 7.6 Return-on-margin and ranking (D8)

Current guard at `orchestrator.py:160`: `r.span_margin = span if span > 0 else None`, and
the annualized return is only computed `if span > 0`. Under incremental margin this makes a
margin-*releasing* trade — the best one available — render as the least informative card.

Change:

- `r.span_margin` keeps the incremental value even when `<= 0`. Do **not** null it.
- Annualized return: compute normally when `incr > 0`. When `incr <= 0`, set
  `annualized_return_pct = None` and set `margin_released = True`.
- Cards with `margin_released` sort **above** all others; within that group sort by
  `net_premium` descending.
- Add badge `"Margin releasing"` to `r.badges`.
- `ranking.score_credit_trade` (`ranking.py:12`) already falls back to
  `max(net_premium, 1.0)` when `span_margin <= 0` — no change needed there, but confirm the
  `None` vs `<= 0` distinction still routes correctly after the change above.

---

## 8. Phase 4 — Fail-safe (D7)

Trigger when `PositionSet.available is False`, **or** `existing_span()` returns `None`.

- `ctx.netting_available = False`; the whole build runs today's standalone path.
- Set `netting_unavailable_reason` on the response.
- `ProposeTradesSuccess` gains `netting_unavailable_reason: str | None`.
- Frontend renders a banner: *"Margin offsets from your open positions could not be applied
  — figures shown are standalone."*

Empty positions (`rows == []`, `available=True`) is **not** a failure. No banner.

---

## 9. Phase 5 — Build-Your-Own and shorts scan

### 9.1 Build-Your-Own

Backend: `POST /strategy-builder/margin` — `route_strategy_builder.py:369`.
Add `net_against_positions: bool = True` to `StrategyBuilderMarginRequest`
(`app/domain/strategy_builder.py:22`). Route resolves the `PositionSet` + `M(P)` and passes
them into `strategy_builder_margin`.

Frontend: `frontend/src/lib/strategy-builder/real-margin.ts`.
`fetchRealBasketMargins` (`:98`) already fans out one call per Sell leg **plus** one basket
call, and already derives `marginBenefit` client-side at `:139` as
`sumStandalone - basket.span` (the *intra-structure* benefit — this candidate's own legs
netted against each other; unrelated to existing positions, and already rendered by
`StrategyLegsPanel`/`BasketLegsPanel`). Change:

- The basket call becomes the netted-against-positions one; its `span` is now incremental
  when positions netting applies.
- **`marginBenefit` (intra-structure, existing metric) must be recomputed from
  `sumStandalone - standaloneSpan`**, not `sumStandalone - basket.span` — subtracting an
  incremental figure from a sum of *standalone* per-leg quotes is apples-to-oranges and
  would silently produce a wrong (often huge or negative) number the moment positions
  netting is active. `standaloneSpan` is the server's new `standalone_span_margin` field
  (§6.3) — always present, exactly the pre-netting basket number.
- A **new**, separate figure — `positionsMarginBenefit` — comes straight from the server's
  `positions_margin_benefit` (§6.3). Do not merge it into `marginBenefit`; they answer
  different questions and both are worth showing (§10.2).
- Keep the per-leg standalone fan-out for the per-leg column, which is unchanged.
- Extend `OnDemandMarginData` (`:86`) with `standaloneSpan`, `positionsMarginBenefit`,
  `nettedAgainstPositions`, `nettedPositionCount`, `nettingUnavailableReason`.
- `prefillSpanMargin` (`:207`) must also carry the new fields, or a prefilled card will show
  stale benefit. Prefer widening it to `prefillMargin(data: Partial<OnDemandMarginData>, forKey)`.

Net API cost: **+1** call per Calculate press (the `M(P)` call), and usually 0 because of
the same-day composition cache.

### 9.2 Covered / uncovered shorts scan

**This surface does NOT route through `strategy_builder_margin`.** It prices each candidate
strike individually via `_resolve_leg_margin_with_source` (`processor.py:610`, called from
the scan loop at `processor.py:1147`).

Change `_resolve_leg_margin_with_source` to accept the same
`existing_legs` / `existing_span_value` kwargs and, on the `breeze_api` branch, send
`existing_legs + [candidate_leg]` and return the incremental. Resolve the `PositionSet` and
`M(P)` **once** in `uncovered_shorts` (`processor.py:865`) before the scan loop, not per
strike.

Call count is unchanged (each existing call just carries more legs), plus the one shared
`M(P)`. On the `exchange_baseline` branch apply the D6 same-expiry filter.

---

## 10. Phase 6 — API contract and frontend

### 10.1 `ProposedTradeOut` — `app/domain/options_strategy.py:52`

```python
standalone_span_margin: Optional[float] = None
positions_margin_benefit: Optional[float] = None   # NOT margin_benefit -- see §6.3
netted_against_positions: bool = False
margin_released: bool = False
```

Mirror in `_result_to_trade_dict` (`orchestrator.py:173`) and in
`frontend/src/lib/strategy-builder/types.ts:199`.

### 10.2 Card display — `ProposedStrategyTradeCard.tsx:203`

The `Margin` stat currently renders `trade.span_margin`. Change to:

- `margin_released` ⇒ show the released amount in the positive/gain colour, prefixed `+`,
  with the badge. **Also show the premium debit on the same card** — released margin is not
  free money; the long legs are paid for in cash. If the card does not already surface net
  premium prominently, add it here.
- Otherwise show incremental as today, with a secondary line when
  `netted_against_positions && positions_margin_benefit > 0`:
  *"₹X saved — netted against N open positions"*.
- When `netted_against_positions` is false, render exactly as today.

### 10.3 Warnings

Reuse the existing `marginWarnings` prop already threaded into `StrategyLegsPanel`
(`frontend/src/app/strategy-builder/page.tsx:1195`) for both the D6 baseline warnings and the
D7 fallback banner.

---

## 11. Tests

`backend/tests/test_strategy_builder_portfolio_margin.py` (new). Use `_FakeBreeze` from
`test_portfolio_netted_margin.py` as the template, and a fresh uuid user id per test so the
composition cache is cold (see the `_uid()` docstring there for why).

| # | Test | Assertion |
|---|---|---|
| T1 | No open positions | Every margin figure and lot count identical to pre-change. `netted_against_positions is False`. **Regression guard — run the whole existing suite too.** |
| T2 | Offsetting position present | `incremental < standalone`; `positions_margin_benefit == standalone − incremental`. |
| T3 | Invariant | `incremental >= −M(P)` across a randomized leg matrix. |
| T4 | Naked short + long hedge candidate | `incremental < 0`, `margin_released is True`, `annualized_return_pct is None`, badge present, sorts first. |
| T5 | Saturation | Incremental at 10 lots > 10 × incremental at 1 lot when the existing position offsets only ~2 lots. Lots chosen must not exceed budget. |
| T6 | Shrink check | Force secant to over-estimate; assert exactly one re-fetch and final `incr + elm <= budget`. |
| T7 | Positions fetch fails | Falls back to standalone, `netting_unavailable_reason` set, lot counts equal T1. |
| T8 | `exchange_baseline` + other-expiry positions | Only same-expiry netted; one `positions_not_netted_other_expiry` warning per dropped row. |
| T9 | Cache key isolation | Same structure, two different position fingerprints ⇒ two distinct margin calls, no cross-contamination. |
| T10 | Cross-expiry netting | Positions in expiry A + candidate in expiry B ⇒ both present in one `margin_calculator` leg list, each with its own `expiry_date`. |
| T11 | Exchange isolation (D10) | BSE positions never appear in an NFO margin call. |
| T12 | Futures excluded (D9) | A futures row in the raw fixture is never sent. (Already filtered by `get_positions`; assert it explicitly.) |
| T13 | Shorts scan | `M(P)` resolved once, not once per strike; per-strike call count unchanged from pre-change. |

Frontend: extend the vitest suites under `frontend/src/lib/strategy-builder/` for the
recomputed `marginBenefit` (now `sumStandalone - standaloneSpan`, §9.1), the new
`positionsMarginBenefit`, and the `margin_released` display branch.

Run: `cd backend && PYTHONPATH=./src .venv/bin/python -m pytest tests/` and
`cd frontend && npm test && npm run lint`.

---

## 12. Production validation gate (blocking)

Local testing **cannot** validate the margin numbers — live `margin_calculator` only works
from the production static IP, and mock mode's implementation is linear so it will always
agree with the linearity assumption.

Before release, on the production instance:

1. **Linearity A/B** (§4): price one structure standalone at 1, 5, and 20 lots. Confirm
   `M(S_N) ≈ N · M(S₁)` within ~1%. If it steps, `standalone` and `benefit` must be
   fetched rather than extrapolated, and the API budget re-planned.
2. **Cross-expiry netting**: confirm ICICI nets a leg list spanning two expiries of one
   underlying in a single call. (Already relied on by the Portfolio page, so expected to
   hold — verify anyway.)
3. **Negative incremental**: with a real naked short open, confirm a hedging structure
   returns `M(P + S) < M(P)`.
4. **Call budget**: confirm a full propose-trades build stays inside the 90/min pacer
   (`app/services/icici_api_pacing.py:19`) and note the build-time regression.

---

## 13. Cost and risk

**API calls per unique structure:** 2 today → 3 when the structure does not overlap the book
(the §7.4 step-2 short-circuit), 4 when it does. Plus one shared `M(P)` per build, usually
served from the same-day cache. Expect noticeably slower builds on scrips where the user holds a
lot; this is the accepted price of D3.

**Main risks:**

1. **Over-sizing** if the secant under-estimates. Mitigated by the §7.5 shrink check, which
   is the last gate before a lot count reaches the user.
2. **Stale positions.** The composition cache is keyed on leg identity, but `get_positions`
   itself has a 15s snapshot TTL. A user squaring off mid-build could size against a
   position that no longer exists. Accepted under D7; the fingerprint in the cache key
   prevents it persisting across builds.
3. **`span_margin_required` semantic flip** (§6.3). Grep for every reader before merging.
4. **Linearity** (§4/§12.1) — the one assumption that could force a redesign of the benefit
   display and the API budget.

---

## 14. Suggested commit sequence

1. Phase 1 primitives + unit tests (no behaviour change).
2. Phase 2 `strategy_builder_margin` netting, default **off** + tests.
3. Phase 5.1 Build-Your-Own wired on (smallest surface, easiest to eyeball).
4. Phase 3 engine sizing + shrink check + tests. **Largest and riskiest — keep it isolated.**
5. Phase 5.2 shorts scan.
6. Phase 6 frontend display + banners.
7. Docs: add a numbered entry to `docs/design-decisions.md` recording D1–D10.

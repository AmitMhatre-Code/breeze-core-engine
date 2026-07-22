# Breeze Terminal Pro — Design Language Specification

> **Read this before writing any UI code.** This document is the single source of truth for
> colors, typography, spacing, shape, and component construction across every Breeze Terminal Pro
> screen. Do not improvise values. Every number, hex code, and font weight here is exact and taken
> directly from the approved design. If a value you need is not here, derive it from the tokens
> below — do not invent a new one.

---

## 0. The #1 rule: use tokens, never raw values in components

Every color in the UI comes from a **CSS custom property (design token)**. Components must never
hardcode a hex code. The tokens are defined once (see §2) and swap automatically between dark and
light theme. If you write `#22D3EE` anywhere except the token definition block, it is wrong — write
`var(--accent)` (or the Tailwind equivalent) instead.

The three mistakes that have been happening, and how this doc prevents them:
1. **Wrong fonts** → §1 gives the exact two families, their weights, and which one goes where. There are only two. Never substitute Inter, Roboto, Arial, or system-ui for the body font.
2. **Wrong colors** → §2 is the complete token table for both themes. Copy it verbatim.
3. **Wrong button/shape treatment** → §5 gives exact, copy-paste recipes for every button, input, card, badge, and toggle, including border-radius and padding in px.

---

## 1. Typography

There are exactly **two typefaces**. No others. Load both from Google Fonts, weights 400/500/600/700.

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
```

### 1.1 IBM Plex Sans — all UI text
Font stack: `'IBM Plex Sans', system-ui, sans-serif`
Use for: labels, headings, body copy, button text, nav items, descriptions, table headers.

### 1.2 IBM Plex Mono — all numbers, codes, identifiers
Font stack: `'IBM Plex Mono', monospace`
Use for **every** value that is numeric or machine-oriented:
- Prices, P&L, quantities, percentages, margins
- API method names (`get_quotes`), parameter names, JSON responses, logs
- Order IDs, session hashes, tokens, dates in tabular contexts, version strings (`v4.2.0`), API counters (`1,284 / 5,000`)

**Rule of thumb:** if a human types it as prose → Plex Sans. If it's a number, a symbol, or something a machine produced → Plex Mono.

### 1.3 Type scale (font-size / weight), exact values in use
| Role | size | weight | family |
|---|---|---|---|
| Screen title (e.g. "Settings") | 21px | 700 | Sans |
| Sub-screen title (e.g. "Broker Credentials") | 18px | 700 | Sans |
| Card/section heading | 13–13.5px | 700 | Sans |
| Nav item (sidebar primary) | 13.5px | 500 (600 active) | Sans |
| Nav item (settings sub-nav) | 13px | 500 (600/700 active) | Sans |
| Body / description | 12px | 400 | Sans |
| Small helper / hint | 11–11.5px | 400 | Sans |
| Micro-label (UPPERCASE) | 10–10.5px | 600–700 | Sans |
| Section eyebrow (UPPERCASE) | 10px | 700 | Sans |
| Primary button | 12.5–13px | 700 | Sans |
| Secondary/ghost button | 11.5–12px | 600 | Sans |
| Input text | 12–13.5px | 400 (600 for numeric) | Mono |
| Table cell (data) | 11.5–12px | 400 | Mono for values, Sans for names |
| Base body default | 14px | 400 | Sans |

Micro-labels above inputs use: `font-size:10.5px; font-weight:600; letter-spacing:.06–.07em; text-transform:uppercase; color:var(--faint)`.
Section eyebrows (group headers in nav) use: `font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--faint)`.

---

## 2. Color tokens (COMPLETE — copy verbatim)

Define these as CSS variables on `:root` (dark, the default) and `.theme-light`. Theme is toggled
by adding/removing the `.theme-light` class on the app root and is persisted to
`localStorage` under the key `breeze-tp-theme` (`"dark"` | `"light"`), re-read on mount.

### 2.1 Dark theme (default) — `:root`
```css
:root {
  --bg:#0A0C10; --panel:#12151C; --panel2:#0F141C; --elevated:#161B24;
  --border:#232A36; --border-soft:#1B222E;
  --text:#E6EAF2; --muted:#8A93A6; --faint:#7B859C; /* --faint retuned for AA — see §2.4 */
  --accent:#22D3EE; --accent-strong:#22D3EE; --accent-ink:#06222A; --accent-tint:#0C2229; --accent-bar:#22D3EE;
  --up:#34D399; --down:#F87171; --up-tint:#0F1F1B; --down-tint:#231518;
  --up-btn:#0EA371; --down-btn:#E5484D;
  --amber:#FBBF24; --amber-tint:#241D0E;
  --gtt:#A78BFA; --gtt-tint:#221E33;
  /* text/icon colour when placed ON the matching -tint wash — see §2.4.
     In dark theme every base already clears AA on its own tint, so these are
     identical to the base tokens. They exist so components can use one rule
     (`on a tint → use -on-tint`) that is correct in BOTH themes. */
  --up-on-tint:#34D399; --down-on-tint:#F87171; --amber-on-tint:#FBBF24;
  --accent-on-tint:#22D3EE; --gtt-on-tint:#A78BFA;
  --shadow:0 18px 50px -20px rgba(0,0,0,.65);
  --track:#1A2330;
}
```

### 2.2 Light theme — `.theme-light`
```css
.theme-light {
  --bg:#EEF1F5; --panel:#FFFFFF; --panel2:#F5F7FA; --elevated:#FFFFFF;
  --border:#DBE1E9; --border-soft:#E8ECF2;
  --text:#0E1520; --muted:#5A6473; --faint:#5F6B7E; /* --faint retuned for AA — see §2.4 */
  --accent:#0891B2; --accent-strong:#0E7490; --accent-ink:#FFFFFF; --accent-tint:#E1F4FA; --accent-bar:#0891B2;
  --up:#0F9D6B; --down:#DC2F44; --up-tint:#E7F6F0; --down-tint:#FCECEE;
  --up-btn:#0F9D6B; --down-btn:#DC2F44;
  --amber:#B45309; --amber-tint:#FBF0DE;
  --gtt:#7C3AED; --gtt-tint:#F1EAFE;
  /* text/icon colour when placed ON the matching -tint wash — see §2.4.
     REQUIRED in light theme: the base tokens FAIL WCAG AA on their own tints
     (--up 3.11:1, --accent 3.25:1, --down 4.05:1, --amber 4.45:1 — all below
     the 4.5:1 small-text floor). These darkened variants clear it with headroom.
     Do NOT "simplify" these back to the base tokens. */
  --up-on-tint:#0C7A53;      /* 4.80:1 on --up-tint     */
  --down-on-tint:#C62A3D;    /* 4.84:1 on --down-tint   */
  --amber-on-tint:#AB4F09;   /* 4.83:1 on --amber-tint  */
  --accent-on-tint:#06738D;  /* 4.82:1 on --accent-tint */
  --gtt-on-tint:#7C3AED;     /* 4.87:1 — already passes, same as --gtt */
  --shadow:0 10px 34px -16px rgba(15,25,40,.20);
  --track:#E3E8EE;
}
```

### 2.3 What each token means (use the RIGHT one — this is where miscoloring happens)
| Token | Meaning / where to use |
|---|---|
| `--bg` | App background (behind everything). |
| `--panel` | Card, sidebar, header surface — one step up from bg. |
| `--panel2` | Recessed surface inside a panel: input backgrounds, inner wells, `<pre>` blocks, segmented-control track. |
| `--elevated` | Floating surfaces above the page: dropdown/combobox popovers, menus. Pair with `--shadow`. |
| `--border` | Default 1px border on cards, inputs, buttons. |
| `--border-soft` | Divider lines *inside* a card (row separators, section splits); also hover fill for ghost controls. |
| `--text` | Primary text. |
| `--muted` | Secondary text, descriptions, inactive nav labels, table values. |
| `--faint` | Tertiary text: micro-labels, placeholders, eyebrows, disabled. |
| `--accent` / `--accent-strong` | Brand cyan. Primary actions, active states, links, focus. `--accent-strong` is the button fill (slightly deeper in light theme). |
| `--accent-ink` | Text/icon color that sits ON an accent-filled surface (dark ink on cyan in dark theme, white on teal in light). |
| `--accent-tint` | Faint accent wash: active nav background, selected dropdown row, subtle highlight. |
| `--accent-bar` | The 2.5px active-indicator bar on nav items. |
| `--up` / `--up-tint` | Positive / gains / "OK"/connected status. Text vs background-wash pair. |
| `--down` / `--down-tint` | Negative / losses / danger / destructive. Text vs wash pair. |
| `--up-btn` / `--down-btn` | Solid button fills for buy (green) / sell + destructive (red). White text on both. |
| `--amber` / `--amber-tint` | Warnings, caution notes, "Funds" risk tier, "Primary" tags. |
| `--gtt` / `--gtt-tint` | GTT risk-tier badge only (purple). |
| `--shadow` | The one and only elevation shadow. Popovers/menus. Cards do NOT get a shadow. |
| `--track` | Slider/progress track background. |
| `--up-on-tint` / `--down-on-tint` / `--amber-on-tint` / `--accent-on-tint` / `--gtt-on-tint` | The text/icon colour to use **when the background is the matching `-tint`**. See §2.4 — this is an accessibility requirement, not a style preference. |

**Semantic pairing rule:** a colored *text/icon* on a plain surface (`--panel`, `--bg`) uses the base
token (`--up`, `--down`, `--amber`, `--accent`). Its *background wash* uses the matching `-tint` —
and text placed **on** that wash uses the matching **`-on-tint`** (§2.4). Never put `--down` text on
a `--up-tint` background, etc.

### 2.4 The `-on-tint` rule (accessibility — do not skip)

> **On a `-tint` background, colour text with the matching `-on-tint` token. Never the base token.**

`-tint` washes are *not* the same lightness as `--panel`, so a base colour that passes WCAG AA as bare
text on a panel can fail on its own tint. In **light theme every base/tint pair failed** the 4.5:1
small-text floor when audited:

| Light pair | Base on tint | With `-on-tint` |
|---|---|---|
| `--up` on `--up-tint` | 3.11:1 ✗ | 4.80:1 ✓ |
| `--accent` on `--accent-tint` | 3.25:1 ✗ | 4.82:1 ✓ |
| `--down` on `--down-tint` | 4.05:1 ✗ | 4.84:1 ✓ |
| `--amber` on `--amber-tint` | 4.45:1 ✗ | 4.83:1 ✓ |
| `--gtt` on `--gtt-tint` | 4.87:1 ✓ | 4.87:1 ✓ (unchanged) |

Dark theme already passes on every pair, so there `-on-tint` == the base token. **Use `-on-tint`
anyway, always** — one rule that is correct in both themes beats a per-theme exception nobody
remembers. `--gtt-on-tint` exists for the same reason: no exceptions to memorize.

**`--faint` (§2.1/§2.2) was separately retuned** away from this doc's original values (light
`#93A0B0` → `#5F6B7E`, dark `#5C6577` → `#7B859C`) for the same reason: it is used for real
label/caption text, and the original values measured 2.48:1 / 3.15:1 against `--panel2`. The
retuned values clear ~5:1. `frontend/src/app/globals.css` has shipped the corrected values for some
time — this doc was the stale copy. **The app is the source of truth for token values; sync this
doc to it, not the reverse.**

**Naming note:** `-ink` and `-on-tint` are **not** the same thing and are not interchangeable.
`--accent-ink` is text on an accent **fill** (white on the solid teal button). `--accent-on-tint` is
text on the accent **wash**. Mixing them up is a contrast bug in one direction and an invisible-text
bug in the other.

---

## 3. Tailwind mapping (this repo uses Tailwind)

Because the codebase is Tailwind + Next.js, expose the tokens through Tailwind so components use
utility classes that resolve to the CSS variables. Add to `tailwind.config.ts`:

```ts
// tailwind.config.ts — theme.extend
colors: {
  bg:        'var(--bg)',
  panel:     'var(--panel)',
  panel2:    'var(--panel2)',
  elevated:  'var(--elevated)',
  border:    'var(--border)',          // note: also wire to Tailwind's `border` default if desired
  'border-soft': 'var(--border-soft)',
  text:      'var(--text)',
  muted:     'var(--muted)',
  faint:     'var(--faint)',
  accent:    'var(--accent)',
  'accent-strong': 'var(--accent-strong)',
  'accent-ink':    'var(--accent-ink)',
  'accent-tint':   'var(--accent-tint)',
  up:        'var(--up)',    'up-tint':   'var(--up-tint)',   'up-btn': 'var(--up-btn)',
  down:      'var(--down)',  'down-tint': 'var(--down-tint)', 'down-btn': 'var(--down-btn)',
  amber:     'var(--amber)', 'amber-tint':'var(--amber-tint)',
  gtt:       'var(--gtt)',   'gtt-tint':  'var(--gtt-tint)',
  // text ON a -tint wash — see §2.4. `text-down-on-tint` etc.
  'up-on-tint':     'var(--up-on-tint)',
  'down-on-tint':   'var(--down-on-tint)',
  'amber-on-tint':  'var(--amber-on-tint)',
  'accent-on-tint': 'var(--accent-on-tint)',
  'gtt-on-tint':    'var(--gtt-on-tint)',
},
fontFamily: {
  sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
  mono: ['"IBM Plex Mono"', 'monospace'],
},
borderRadius: {
  // see §4 — map to the exact radii in use
  DEFAULT: '7px', md: '8px', lg: '9px', xl: '13px', pill: '999px',
},
boxShadow: {
  pop: 'var(--shadow)',   // dropdowns/menus only
},
```

Put the two `:root` / `.theme-light` blocks from §2 into `globals.css`. Keep the tokens as CSS
variables (not Tailwind-native color values) so a single class swap flips the whole theme.

Now `bg-panel`, `text-muted`, `border-border`, `bg-accent-strong text-accent-ink`, `font-mono`,
`rounded-xl`, `shadow-pop` all mean exactly what this spec says. **Prefer these classes over inline
styles in production**, but the values must match this doc.

---

## 4. Shape, radius & spacing scale

Radius is deliberate and consistent. Memorize this ladder — the wrong radius is a common tell:

| Element | border-radius |
|---|---|
| Cards / major sections | **13px** |
| Inner boxes inside a card (param box, status card, feed card) | 9–11px |
| Buttons (primary & most) | **8px** (small header buttons 8px, large form submit 9px) |
| Inputs / textareas / selects | 7–9px (text inputs 7px inside tables, 9px for standalone form fields) |
| Small ghost buttons (Copy, Edit key, Remove) | 6–7px |
| Segmented-control track | 9px; the buttons inside it 6px |
| Icon buttons (header) | 8px |
| Screen icon chip (38×38) | 10px |
| Logo mark | 8px (sidebar 34px), 10px (login 44px) |
| Pills / badges / status dots container | 999px (fully round) |
| Active nav indicator bar | 999px |

Spacing: cards use `padding:20px`. Inner boxes use `padding:12–16px`. Gaps between stacked
form elements: `gap:10–14px`. Row padding in tables: `8–9px 10–14px`. Icon-to-label gap in nav: `10–11px`.

Borders are **always 1px**. Card border `var(--border)`; in-card dividers `var(--border-soft)`.
Cards have **no drop shadow** — separation comes from the border and the `--panel` vs `--bg`
contrast. Only floating popovers get `--shadow`.

---

## 5. Component recipes (copy-paste, exact)

All examples are inline-styled for unambiguous values. In production, convert to the Tailwind
classes from §3 — but the resolved values must equal these.

### 5.1 Primary button (accent)
The default call-to-action. Cyan fill, ink-colored text, no border, weight 700.
```html
<button style="font-size:12.5px; font-weight:700; color:var(--accent-ink); background:var(--accent-strong); border:none; border-radius:8px; padding:9px 16px; cursor:pointer;">Save</button>
```
- Large form submit variant: `font-size:13px; border-radius:9px; padding:11px 20px;`
- Small header variant (e.g. "Save all"): `font-size:12px; padding:8px 14px;`
- Hover: `filter: brightness(1.06)` (see `.execbtn` pattern) — optional but preferred.

### 5.2 Secondary / ghost button (outline)
Transparent fill, 1px border, text color `--text`, weight 600.
```html
<button style="font-size:12px; font-weight:600; color:var(--text); background:transparent; border:1px solid var(--border); border-radius:8px; padding:9px 14px; cursor:pointer;">Save schedule</button>
```
- Small variants (Copy, Edit key, Add key): `font-size:11–11.5px; border-radius:6–7px; padding:4px 10px` … `6px 12px`.
- Hover: `background: var(--border-soft)`.

### 5.3 Destructive button (red)
For "Delete account", and the playground "Fire API" (because most calls are live). White text on red fill.
```html
<button style="font-size:13px; font-weight:700; color:#fff; background:var(--down-btn); border:none; border-radius:9px; padding:11px 20px; cursor:pointer;">Delete account</button>
```

### 5.4 Buy / Sell buttons (order tickets)
Buy: `background:var(--up-btn); color:#fff`. Sell: `background:var(--down-btn); color:#fff`. Same shape as primary.

### 5.5 Text input / number input
Recessed `--panel2` fill, 1px border, mono font (numbers), fixed height.
```html
<input type="text" style="width:100%; font-family:'IBM Plex Mono',monospace; font-size:13.5px; color:var(--text); background:var(--panel2); border:1px solid var(--border); border-radius:9px; padding:0 12px; height:40px;">
```
- In-table compact input: `font-size:12.5px; border-radius:7px; padding:0 10px; height:32px;`.
- Disabled/read-only field: `color:var(--faint); border:1px dashed var(--border); cursor:not-allowed;`.
- Micro-label sits ABOVE the input (see §1.3), not inside.
- Focus (recommended): `border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent);`.

### 5.6 Textarea
Same as input but `padding:6px 10px; resize:vertical;` and `font-size:11.5px` for JSON/prompt fields.

### 5.7 Card / section
```html
<section style="background:var(--panel); border:1px solid var(--border); border-radius:13px; padding:20px; display:flex; flex-direction:column; gap:14px;"> … </section>
```
Danger card: `border:1px solid color-mix(in srgb, var(--down) 35%, var(--border));`.
Warning/caution card: `border:1px solid var(--amber); background:var(--amber-tint);`.

### 5.8 Status / risk badge (pill)
```html
<span style="font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--up-on-tint); background:var(--up-tint); padding:2px 8px; border-radius:999px;">Configured</span>
```
Swap the on-tint/tint pair per meaning: OK/connected → up; warning/Funds → amber; danger/Trade → down;
GTT → gtt; neutral → `color:var(--faint); background:var(--panel2)`.

The text token is **`-on-tint`, never the base** (§2.4) — badge text is 10.5px, i.e. small text, so it
needs 4.5:1. Neutral stays on `--faint`, which clears AA at the retuned values in §2.1/§2.2
(5.03:1 light / 4.99:1 dark against `--panel2`).

### 5.9 Inline note / callout (full-width, not a pill)
Warning note: `font-size:11px; line-height:1.6; color:var(--amber-on-tint); background:var(--amber-tint); border:1px solid color-mix(in srgb, var(--amber) 40%, transparent); border-radius:8px; padding:8px 10px;`.
Danger note: same structure with `--down-on-tint` text / `--down-tint` background.

Text is `-on-tint` (§2.4) — at 11px this is small text and needs 4.5:1. The **border** may keep using
the base token via `color-mix`, since a 1px border is a non-text element (3:1 floor).

### 5.10 Segmented control (tabs like API-wise / Route-wise)
Track is `--panel2` with a 1px border and `padding:3px`; the active segment is an accent-filled button, inactive are transparent with `--muted` text. All mono, 11.5px, weight 600.
```html
<div style="display:inline-flex; padding:3px; gap:2px; background:var(--panel2); border:1px solid var(--border); border-radius:9px; width:fit-content;">
  <button style="font-family:'IBM Plex Mono',monospace; font-size:11.5px; font-weight:600; color:var(--accent-ink); background:var(--accent-strong); border:none; border-radius:6px; padding:6px 14px; cursor:pointer;">API-wise</button>
  <button style="font-family:'IBM Plex Mono',monospace; font-size:11.5px; font-weight:600; color:var(--muted); background:transparent; border:none; border-radius:6px; padding:6px 14px; cursor:pointer;">Route-wise</button>
</div>
```

### 5.11 Toggle switch
44×24px track, 20px white knob, 2px inset; ON = `--accent-strong` track + knob at `left:22px`, OFF = `--border` track + knob at `left:2px`; `transition:left .15s ease`.
```html
<button style="width:44px; height:24px; border-radius:999px; border:none; cursor:pointer; position:relative; background:var(--accent-strong);">
  <span style="position:absolute; top:2px; left:22px; width:20px; height:20px; border-radius:999px; background:#fff; transition:left .15s ease;"></span>
</button>
```

### 5.12a Checkbox (NOT native OS checkbox chrome)
Native `<input type="checkbox">` renders unstyled OS chrome (a round-ish, differently-shaped box
per browser/OS) that breaks the dark theme even when colored via `accent-color`. Always use the
shared `<Checkbox />` component (`frontend/src/components/ui/Checkbox.tsx`) — never a raw
`<input type="checkbox" className="...">`. It renders a flat **16×16px square**: `border-border` +
`bg-panel2` unchecked, `bg-accent-strong` + `border-accent-strong` checked, with an inline SVG
check mark in `--accent-ink` (no native browser tick) and an indeterminate-state dash. An ESLint
rule (`no-restricted-syntax` in `frontend/eslint.config.mjs`) enforces this — a bare
`type="checkbox"` fails lint outside of `Checkbox.tsx` itself. Reference usage: the Orders page
row-select checkboxes.
```tsx
<Checkbox checked={value} onChange={setValue} aria-label="…" />
```

### 5.12 Custom dropdown / combobox (NOT a native `<select>`)
Native selects render an OS-themed popup that breaks the dark theme — the Settings screens use a
custom dropdown everywhere a picker is needed. Pattern:
- **Trigger**: full-width button, `background:var(--panel2)`, `border:1px solid var(--border)` (→ `var(--accent)` when open), `border-radius:9–10px`, `padding:9–12px`, chevron SVG on the right that rotates 180° when open. When open add ring: `box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent)`.
- **Panel**: absolutely positioned below the trigger, `background:var(--elevated)`, `border:1px solid var(--border)`, `border-radius:10–11px`, `box-shadow:var(--shadow)`, `z-index:50`. A transparent fixed full-screen backdrop (`position:fixed; inset:0; z-index:40`) sits behind it to catch outside-clicks.
- **Rows**: `padding:8px 9px; border-radius:7–8px; cursor:pointer;` hover `background:var(--panel2)`. Selected row: `background:var(--accent-tint)`, weight 700, with a check icon in `--accent-strong`.
- **Grouped rows** (e.g. API risk tiers): uppercase group header `font-size:10px; font-weight:700; letter-spacing:.08em; color:var(--faint); padding:10px 8px 6px;`, and each item may carry a short colored risk badge on the right.
- Optional search input at the top of the panel: standard text input styling, `font-family:'IBM Plex Sans'`.

### 5.13 Data table
Header row: `background:var(--panel2)`, cells `font-size:10–11.5px; font-weight:700; color:var(--faint)` (uppercase for compact tables), `padding:9px 10px`, left-aligned (numeric columns right-aligned). Body rows: `border-top:1px solid var(--border-soft)`, cell `padding:8px 10px`, values in `font-mono`, names in Sans. Row hover: `background:var(--panel2)` (`.datarow` pattern). Wrap the table in a `border:1px solid var(--border); border-radius:10px; overflow:hidden` (or `overflow-x:auto` when it can exceed width, with `min-width` on the table).

### 5.14 `<pre>` / log / response blocks
`background:var(--panel2); border:1px solid var(--border); border-radius:9px; padding:12px; font-family:'IBM Plex Mono',monospace; font-size:10.5–11px; color:var(--muted);` and **always** `white-space:pre-wrap; word-break:break-word;`. Their grid/flex parent needs `min-width:0` or the layout overflows horizontally.

---

## 6. Layout shell (identical on every app screen)

```
┌────────────┬──────────────────────────────────────────────┐
│  SIDEBAR   │  HEADER (52px)                                │
│  236px     ├──────────────────────────────────────────────┤
│  fixed     │  MAIN (scroll)                               │
│            │    max-width:1280px, centered,               │
│            │    padding:22px 24px 44px                    │
└────────────┴──────────────────────────────────────────────┘
```

### 6.1 Sidebar — `width:236px`, `flex:0 0 auto`, `border-right:1px solid var(--border)`, `background:var(--panel)`, `padding:16px 12px`, column flex.
- **Brand row**: `assets/breeze-logo.png` at 34×34, `border-radius:8px`; next to it a two-line lockup — "Breeze" (16px/700, `letter-spacing:-.01em`) over "Terminal" (10px/600, uppercase, `letter-spacing:.08em`, `--faint`).
- **Nav items**: `display:flex; align-items:center; gap:11px; padding:9px 11px; border-radius:7px;` label 13.5px. Inactive: `color:var(--muted)`. Hover: `background:var(--panel2); color:var(--text)`. **Active**: `background:var(--accent-tint); color:var(--text); font-weight:600;` plus a 2.5px `--accent-bar` pill on the left edge (`position:absolute; left:0; top:9px; bottom:9px; width:2.5px; border-radius:999px`).
- Icons are inline Lucide-style stroke SVGs, 18px, `stroke-width:1.9`, `stroke="currentColor"`.
- A "NEW" badge (e.g. Basket Order): `font-size:9px; font-weight:700; letter-spacing:.06em; color:var(--accent-ink); background:var(--accent); padding:1.5px 5px; border-radius:4px`.
- **Bottom pinned** (`margin-top:auto`): "Session" eyebrow + a status line with a pulsing green dot (`--up`, `box-shadow:0 0 8px var(--up)`).

### 6.2 Header — `height:52px`, `border-bottom:1px solid var(--border)`, `background:var(--panel)`, `padding:0 20px`, space-between.
- Left: an uppercase context eyebrow, the account name (14px/600), and a labeled figure (e.g. "Free margin" + a mono value in `--up`).
- Right cluster: mono API counter (`1,284 / 5,000` with `--faint` label), a connection status dot, mono version string in `--accent-strong`, a **theme toggle icon button** (sun in dark theme, moon in light — icon rotates on hover), and a log-out icon button. Icon buttons: 34×34, `border-radius:8px`, transparent, `--muted`, hover `background:var(--border-soft); color:var(--text)`.

### 6.3 Settings-specific: persistent sub-nav (NOT modals, NOT routes)
Inside MAIN, Settings is a two-column layout: a **252px sticky left sub-nav** + the content pane.
Selecting an item swaps the right pane via client state — **no route change, no dialog/modal** (this
was an explicit product requirement; the old popup pattern is removed). Sub-nav is grouped with
uppercase eyebrows: (ungrouped: Broker Credentials, Quantity Limits, API Usage) · **Automation**
(GenAI Settings, Reference Data Loads, Exchange Calendar) · **Diagnostics** (Audit Logs) · **Danger
zone** (API Playground, Delete Account). Danger-zone eyebrow and its items use `--down`; active/hover
danger items use `--down-tint` and a `--down` left bar instead of accent.

---

## 7. Iconography
- All icons are **inline SVG, Lucide-style**: `fill:none; stroke:currentColor; stroke-width:1.9` (sidebar/nav) up to `2.4` (small glyphs), `stroke-linecap:round; stroke-linejoin:round`. Sizes 14–19px in-line, 18px in nav.
- Icons inherit color from `currentColor` — set the parent's `color` token; don't hardcode stroke colors except when an icon must stay on-brand inside a filled chip (then use `--accent-ink` / the semantic token).
- **Screen header icon chip**: 38×38, `border-radius:10px`, `background:var(--accent-tint)` (or `--down-tint` for danger screens), containing an 18px icon in `--accent-strong` (or `--down`).
- The only raster asset is the logo (`assets/breeze-logo.png`, the real ICICI-Breeze "B" mark from the repo at `frontend/src/app/android-chrome-192x192.png`). Never redraw or substitute it.
- No emoji anywhere.

---

## 8. Motion
- Transitions are subtle and fast: `.15s ease` for background/color/position (toggles, hovers, dropdown open), `.2s ease` for icon rotation.
- Status dots that indicate "live/connected" pulse via a soft `box-shadow` glow in the semantic color.
- No large entrance animations, parallax, or decorative motion.

---

## 9. Hard "don'ts" (these are the failure modes to avoid)
1. **Don't** use any font other than IBM Plex Sans (UI) and IBM Plex Mono (numbers/code).
2. **Don't** hardcode hex codes in components — only `var(--token)` / the mapped Tailwind class.
3. **Don't** use native `<select>` — build the custom dropdown in §5.12 so it respects the theme.
3a. **Don't** use a raw native `<input type="checkbox">` — use `<Checkbox />` (§5.12a); lint enforces this.
4. **Don't** give cards drop shadows; only popovers/menus get `--shadow`.
5. **Don't** use gradients, glassmorphism, or rounded-corner-with-left-accent "AI-slop" callouts. Accent bars belong only on the active nav item.
6. **Don't** mix radii randomly — follow the ladder in §4 (cards 13, buttons 8, inputs 7–9, pills 999).
7. **Don't** put settings sub-screens in modals or separate routes — they're an in-place pane swap.
8. **Don't** forget `white-space:pre-wrap; word-break:break-word` on log/response `<pre>` blocks and `min-width:0` on their grid parents, or the layout overflows.
9. **Don't** color numbers with the body text token when they carry P&L meaning — gains `--up`, losses `--down`, otherwise `--text`/`--muted`.
10. **Don't** substitute the logo with a drawn icon.

---

## 10. Quick reference card (pin this)
- Fonts: **IBM Plex Sans** (text) + **IBM Plex Mono** (numbers/code). Nothing else.
- Accent: `#22D3EE` cyan (dark) / `#0E7490` teal (light) → token `--accent` / `--accent-strong`, text on it `--accent-ink`.
- Surfaces: `--bg` → `--panel` → `--panel2` (recessed) → `--elevated` (floating).
- Buttons: primary = accent fill + `--accent-ink` + 700 + radius 8; ghost = transparent + 1px `--border` + 600; danger = `--down-btn` + white.
- Cards: `--panel`, 1px `--border`, radius **13**, padding 20, **no shadow**.
- Inputs: `--panel2`, 1px `--border`, radius 7–9, mono, height 32–40.
- Checkboxes: always `<Checkbox />` from `components/ui/Checkbox.tsx` — never raw `type="checkbox"`.
- Radii ladder: card 13 · button 8 · input 7–9 · small ghost 6 · pill 999.
- Semantic pairs: up/`up-tint`, down/`down-tint`, amber/`amber-tint`, gtt/`gtt-tint`, accent/`accent-tint`.
- **Text on a `-tint` uses `-on-tint`, never the base token** (§2.4). Neutral badge = `--muted` on `--panel2`, not `--faint`.
- `-ink` (text on a solid **fill**) ≠ `-on-tint` (text on a **wash**). Not interchangeable.
- Shell: sidebar 236 · header 52 · content max-w 1280 · settings sub-nav 252 (sticky).

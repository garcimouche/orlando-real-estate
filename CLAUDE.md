# Orlando Real Estate — Context for Claude Code

Franck Garcia's STR Airbnb investment tooling for Orlando/Kissimmee. Quebec
investor, targets short-term rental properties. See the `orlando-investor-profile`
skill for full investor context (budget, tax situation, preferences).

## Architecture

Three pieces, no build step:

1. **`src/property_finder.py`** — Realtor.com (RealtyInUS) discovery. Lists
   properties, scores them for STR fit, optionally enriches with detail calls.
   Responses are cached on disk to avoid burning through the provider quota.
   - `python3 property_finder.py` → live run (list + enrich + cache)
   - `python3 property_finder.py --local` → replay from cache, no network
   - Module-level `KNOWN_RESORTS` dict + `identify_resort(text)` helper
     (longer keys first for specificity).
   - `_has_explicit_str_signals(prop)` rejects zip-only matches — it requires
     keywords or a known resort name.
   - `export_scored_properties(..., require_explicit_signals=True)` is the
     default used for the finance view export.

2. **`src/property_enricher.py`** — Per-property detail fetch. After merging
   detail data it re-runs `identify_resort()` over
   `address + full_description + str_keywords_found` so resorts mentioned only
   in the MLS description are recovered. The import of `identify_resort` is
   **lazy inside `_merge_detail()`** to avoid a circular import
   (property_finder already imports PropertyEnricher).

3. **`src/property_finance.html` + `src/property_finance.jsx`** — React 18 UMD
   + Babel Standalone runtime JSX transform. No bundler. The HTML fetches the
   JSX as text, rewrites the `import { ... } from "react"` line into
   `const { ... } = React;`, strips `export default`, then compiles via
   `Babel.transform(..., { presets: ["react"] })`.
   - Served by `./src/start_web_finance.sh`, viewed at
     http://localhost:8000/src/property_finance.html
   - Theming: CSS custom properties gated on
     `:root[data-theme="dark"|"light"]`. A pre-mount inline script in the HTML
     reads `localStorage.theme` and sets `data-theme` before React renders, to
     prevent flash. Default is **light**. Accent colors (teal, green, blue,
     gold, red, yellow) stay constant across themes; only neutrals flip.
   - `ThemeToggle` (🌙 / ☀️) lives next to the property selector at the top.
   - `#root` is 80% viewport wide, capped at 1280px.

## Working conventions

- **Commits: on explicit request only.** Do not auto-commit. When asked,
  prefer per-feature granularity.
- **Language**: UI strings are French; code identifiers English. Replies to
  Franck can mix FR/EN — follow his lead per message.
- **No new markdown docs** unless requested. The existing `README.md` is the
  user-facing doc.

## Recent work (this session)

- STR filter now drops zip-only properties on export (explicit-signals
  default).
- Resort detection lifted 0/14 → 17/20 by re-running after enrichment against
  the full MLS description.
- HOA slider max raised 600 → 1200.
- Main view widened to 80% / 1280px cap.
- STR-signals card moved above the Score-STR card on the Discovery tab.
- Light/dark theme toggle added, neutral CSS tokens throughout the JSX
  (86 hex codes migrated to `var(--token)`).
- README.md added (French, two sections: cueillette / analyse).

## Pending — cashflow column in property selector

User confirmed ("yes I would like that") in response to the offer:

> Veux-tu que j'ajoute une colonne « cashflow estimé @ AirDNA » au sélecteur
> de propriétés ? Ça te permettrait de trier par rentabilité au lieu de par
> score.

**Not yet implemented.** Plan:

1. Add `estimateCashflow(p)` module-level helper in `property_finance.jsx`
   near the other helpers (after `calcSolde`, before `Slider`). It should
   mirror `computeBase` with default slider values:
   - `mf = 30` (mise de fonds %)
   - `tHypo = 7.5`
   - `tGestion = 20`
   - `assurance = 180`
   - `maintenance = 150`
   - `hoa` → `p.hoa_fee_monthly ?? 300`
   - `prix` → `p.price`
   - `revBruts` → `p.estimated_monthly_gross`
   - `tOcc` → `p.estimated_occupancy`
   Returns `revNet − chargesOp − hypo` where
   `revNet = revBruts × tOcc / 100`.

2. `App` state: `sortBy` with localStorage persistence, default `"score"`.
   Values: `"score" | "cashflow"`.

3. `useMemo` for `sortedProperties` — attach `_cashflow` to each item; sort
   desc by cashflow when `sortBy === "cashflow"`.

4. Preserve the currently selected property across sort flips by tracking
   its id (not its index).

5. `PropertySelector`:
   - Header card: cashflow value on the right, green if ≥ 0, red if < 0.
   - Dropdown items: cashflow on the right per row.
   - Sort toggle pill in the header, e.g. "Trié par : Score ▾" ↔
     "Trié par : Cashflow ▾".
   - The `#N` rank stays tied to original STR-score rank (don't renumber
     when sorted by cashflow).

Per the user's standing rule, **leave this uncommitted after implementing**.

## Handy file pointers

- `src/property_finance.jsx`
  - `computeBase(...)` — finance math (line ~108)
  - `revBruts` state default `useState(4200)` (line ~505)
  - `useEffect` resetting `prix / hoa / revBruts / tOcc` from
    `selectedProp` (lines ~524–536)
  - The two gauges on the monthly tab (lines ~801–802): `charges/revenus`
    and `occupation seuil rentabilité` — break-even formula is
    `(chargesOp + hypo) / revBruts × 100`.
- `src/property_finder.py` — `KNOWN_RESORTS`, `identify_resort`,
  `_has_explicit_str_signals`, `export_scored_properties`.
- `src/property_enricher.py` — lazy `identify_resort` re-scan inside
  `_merge_detail`.

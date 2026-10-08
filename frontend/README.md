# Developer Dashboard: Profile, Apartments And Sales

Просмотр на текущей машине: http://localhost:5173.
Утверждённое оформление сохранено; профиль подключён к API и полному экспорту Excel.
Дорожная карта: `../docs/dashboard-roadmap.md`.

React + TypeScript + Vite, ECharts canvas charts and Lucide controls. API is the
default data mode. Profile, the two apartment pages and sales run locally in React;
other pages remain in Streamlit. No backend or data files
are changed by this frontend.

## Run

- `pnpm install` (or `npm install`)
- `pnpm dev` (or `npm run dev`), normally `http://127.0.0.1:5173`
- `pnpm build` / `pnpm typecheck`
- `pnpm test` (Node 24, native TypeScript type stripping)
- `node --test src/data.test.mjs` (API contract and data-mode tests)
- `node src/live-api.browser.cjs http://127.0.0.1:5173` (Playwright fixture checks)
- `node --test src/apartments.test.mjs` (apartment contract and data tests)
- `node src/apartments.browser.cjs http://127.0.0.1:5173` (apartment browser fixtures)
- Set `DASHBOARD_CHECK_LIVE=1` for apartment browser checks against port 8000 as well.
  Playwright is available in the bundled Node packages; set `NODE_PATH` to
  `C:/Users/Mihail/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`.

On this workspace host, pnpm is bundled at
`C:/Users/Mihail/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd`.
From the repository root, `scripts/start_dashboard_preview.ps1` also launches the
preview after dependencies are installed and chooses a free port. The current
verified session was started directly with Vite; the launcher was syntax-checked.

The frozen snapshot is produced and owned by the baseline worker. Do not edit
it in the frontend. Contract: `../docs/dashboard-baseline.md`, schema version 1.

## Data Modes And Proxy

Start the backend separately on port 8000. Vite proxies `/api` to
`http://127.0.0.1:8000`; set `DASHBOARD_API_TARGET` in the process environment or
`frontend/.env.local` to override it. The proxy applies to dev and preview.
Production hosting must reverse-proxy `/api` to the backend on the same origin.
`DASHBOARD_API_TARGET` configures Vite only, not a deployed browser API origin.

The default mode fetches `/api/v1/catalog`, then
`/api/v1/profile?developer=<normalized key>&region=<msk|rf>`. The catalog
supplies the complete developer selector and available apartment regions
(empty regions default to Moscow). Failed requests show an error and retry;
there is no automatic snapshot fallback. Changing company/region aborts stale
requests and immediately hides the previous profile. Filters stay available
while the selected profile loads or fails. Changing years does not request API
data and cannot truncate the underlying full profile.
New profile versions reconcile the selector from the response controls. A 404
refreshes the catalog automatically once per company/region pair; persistent
404s remain visible rather than looping. Retry reloads the catalog and profile.

Only explicit `VITE_DATA_MODE=snapshot` uses `public/profile-snapshot.json`.
Restart Vite after changing env settings; build-time mode is embedded in the
production bundle. Snapshot mode visibly says `Зафиксированный срез` and
disables full-profile Excel. API mode visibly says `Данные API`, which does not
claim that the underlying source files were updated today.

## Behavior

The first page is the actual developer profile. Developer, apartment region and
annual chart year bounds persist in URL parameters `developer`, `region`,
`from`, `to`, including browser back/forward. Regions and years come from the
catalog/profile. Apartment region does not affect Moscow monitoring, ratings, sales,
delays or escrow; every section names its own scope.

Dark is the default theme; theme, sidebar width and each nested navigation
expansion persist locally. Profile and apartment links use local navigation,
active sidebar state and close the mobile menu. Menu navigation restores each
page's last URL query within the current app session. Apartment pages share
compatible region/raw developer filters; normalized profile IDs stay separate.
Other sidebar pages open the existing Streamlit
server at `localhost:8501` in a separate tab. Its implicit routes strip the
numeric filename prefixes.

Each chart offers segment hover, clickable swatch legends, a collapsible data
table, CSV and PNG. Apartments, delays and full object registers export CSV.
Object registers support search and pagination; export includes every source
row. The Excel icon next to Streamlit downloads the full backend workbook from
`/api/v1/profile/export` using company/region and the displayed
`required_version`, regardless of chart years,
table search, pagination or hidden chart series. Download errors allow retry
and switching profile cancels an unfinished download. HTTP 409 means the data
generation changed: no file is downloaded and the user is prompted to reload.
No values are generated or substituted by this UI. Missing values remain
null/dashes. Delay display fields intentionally preserve the original page's
dash semantics and uncertain reporting-period warnings.

Source dates are the API/snapshot's original metadata dates, not a claim of live
freshness. Source details distinguish the generation date and source-file
modification metadata. Live `candidateRawFiles` are shown as registry candidates,
not proof of reads or contribution. Profile `quality.source` paths are shown
separately as reported source selections (not an exhaustive read log). Frozen
snapshots continue to use `openedInputs`. The API version is retained.
Nonfatal `provenance.issues` diagnostics remain visible in source notes.

## Apartment Pages

`/` remains the profile. `/apartments` shows the region overview;
`/apartments/developer` shows one developer. Both reuse the approved header,
sidebar, themes, table and ECharts controls. Apartment routes always use the
API, even when the profile is explicitly in snapshot mode.

The catalog is `/api/v1/apartments/catalog`. Overview and detail requests use
`/api/v1/apartments?region=msk|rf` and
`/api/v1/apartments/developer?region=msk|rf&developer=<exact source name>`.
The selector uses region-specific catalog lists, preserves raw IDs and keeps
the first entry for any repeated exact ID. Overview tables retain all source
rows, including duplicates and the API's Moscow-first regional ordering.
Failures show retry with no demo fallback. Filter changes abort stale requests
and immediately hide old results. A 404 refreshes the catalog once per filter
pair; a changed response version reconciles the catalog before showing data.
Persistent mismatches become visible errors, not refresh loops.

Region and developer filters are stored in the URL. Volume tables have search,
pagination (50 rows by default, also 20/100), and URL parameters
`developersSearch/Page/Size`, `regionsSearch/Page/Size` and
`comparisonSearch/Page/Size` (each suffix is part of a separate parameter).
Back/forward and reload restore these filters; out-of-range pages reconcile
to the available results. Selecting a new region keeps the exact developer ID
when available in the destination catalog, otherwise selects its first entry;
table page numbers reset. Returning through the menu restores each apartment
page's search, page and page-size parameters while keeping shared apartment
region/developer state. Profile company keys never transfer to these selectors.

Overview apartment counts are in units, while developer counts are in thousands.
All apartment areas are already in thousands of square meters; the UI does not
divide them again. Detail KPIs, rank, market base and reference averages come
from the backend summary. Detail area uses zero decimal places, count/average
one, and market share two. Independent nulls remain dashes. Room colors are
fixed to green/blue/amber/red by type even when another type is missing.
Room-strip widths alone are normalized visually; source percentages remain
unchanged in tables, tooltips and CSV. Donut hover reports source shares, not
the normalized angle percentage. The selected developer is highlighted in
the backend's top-10-plus-selected comparison, including ranks outside top 10.

Table CSV includes every API row, regardless of search/page filters. Chart CSV
includes all source bins/room values; PNG exports the current ECharts view.
Full Excel uses `/api/v1/apartments/export?region=...&developer=...&required_version=...`
(developer is omitted for overview), pinned to the displayed response version.
Search, pagination and chart visibility never enter the Excel request. HTTP 409
downloads nothing and asks for a reload; switching scope cancels a pending file.
Report dates and source dates come from the API, not today's date. Source
warnings are visible outside collapsed metadata. Candidate files are explicitly
identified as candidates, not evidence of reads. Empty tables and regions with
no developer entries remain valid empty states.

Apartment verification: `pnpm test` includes the focused contract tests without
changing profile data or snapshot tests. The apartment browser script checks
fixture failures/retry, bounded 404/version reconciliation, aborts, exact raw
names, URL history, complete exports, nulls, colors, outside-top-10 highlighting,
bar/donut hover, nonblank canvas, both themes and widths 360/390/430/768/1280/1920.
With `DASHBOARD_CHECK_LIVE=1`, it also checks both live overviews, first/rank-11/last
developers in each region, summary values and four actual Excel downloads.
Screenshots and `checks.json` go to the OS temporary directory
`dashboard-apartments-checks` (override with `APARTMENTS_OUTPUT`).

## Sales And Readiness

`/sales` reuses the approved shell and always reads `/api/v1/sales/catalog`, then
`/api/v1/sales?region=...&period=YYYY-MM`. Moscow is the initial region when
available. Missing or unavailable periods default to the latest available month
in the selected region; switching region selects that region's latest month.
The region select, period select and month slider use catalog entries only.
URL parameters `region`, `period` and `table` restore selection on reload and
history navigation. Each segment retains its own `sales<API table id>Search`,
`sales<API table id>Page` and `sales<API table id>Size` parameters. Menu navigation
restores the complete sales query without importing profile/apartment filters.
Tabs support arrow keys, Home and End; all table rows are searchable/paginated.

Four KPI values, units and precision come from the API without conversion.
The forecast retains the legacy shared numeric axis, including mixed series units
and source category order; it is not normalized or moved to a second axis.
Monthly volume and percentage charts remain separate. Monthly categories align
chronologically across uneven series without dropping repeated months; CSV keeps
original point order. Forecast bars show
individual tooltips; monthly charts use unified hover so overlapping markers
cannot hide another series. Tooltip values include the series' own unit.
Nulls remain missing markers/dashes, and lines never connect across nulls.
Segment tables preserve raw strings (including percentages and dates), empty
strings, numbers, nulls, all columns and all rows without reparsing their units.

Table CSV exports every row in the active segment regardless of search/page.
Each chart exports all original points as period/year, series, unit and value;
hidden series and null points remain in CSV. PNG reflects the current chart view.
Excel uses `/api/v1/sales/export?region=...&period=...&required_version=...`, pinned
to the displayed generation, without table or chart filters. HTTP 409 downloads
nothing and asks for reload. Scope changes cancel pending Excel downloads.
Data requests abort stale responses and hide old results immediately. HTTP 404
refreshes the catalog once per filter pair; version reconciliation is bounded per
catalog generation, allowing successive generation changes on the same filters.
Persistent mismatch, schema, HTTP and network errors remain visible with retry;
there is no demo fallback. Source warnings are visible outside metadata details;
source dates, generation date, version and candidate paths retain API provenance.

Verification commands: `node --test src/sales.test.mjs` and
`node src/sales.browser.cjs http://127.0.0.1:5173`. Set `NODE_PATH` as above for
Playwright and `DASHBOARD_CHECK_LIVE=1` to additionally check both live regions,
earliest/latest periods, all six full table CSVs and four real Excel downloads.
The browser script covers fixtures, error/retry, sequential generations on the
same filter pair, persistent mismatch/404 bounds, stale responses, URL history,
reload, local navigation, keyboard tabs, exports/409, missing data/dates,
both themes, every series hover and canvas/overflow at 360–1920 px.
Screenshots and `checks.json` are saved to `%TEMP%/dashboard-sales-checks`;
override the directory using `SALES_OUTPUT`. Workbook calculations/parity are
owned and checked by the backend agent, not claimed by these frontend tests.

## Brand Assets

`public/brand-moscow.svg`, `brand-gk.svg` and `brand-dgp.svg` reuse the existing
SVG geometry from `_to_delete/rynok_nedvizhimosti_2026-2.html` (`LOGO_GERB`,
`LOGO_GK`, `LOGO_DGP`). The presentation describes these as stylized assets;
they are not newly fabricated or certified official logos.

## Verification

`tests/snapshot.test.mjs` verifies schema rejection, area sums excluding year
metadata, exact available developer/region options, apartment-only regional
scope, all six real delay displays, source null comparisons, complete objects,
unit conversion, and preservation of source dates. Browser verification is
coordinated by the parent task through `scripts/check_dashboard_frontend.cjs`
against the real API. `src/data.test.mjs` checks catalog/profile API validation,
normalized selector keys, region fallback, unit/null/source preservation, full
Excel query scope and explicit frozen mode. `src/live-api.browser.cjs` uses
contract fixtures to exercise loading, errors/retry, stale requests, URL
back/forward, local years, Excel requests, responsive overflow and segment
hover. Backend workbook content and calculation parity remain backend checks.

The source snapshot retains original display captions for baseline verification.
The frontend labels the first input donut with its actual covered years and
discloses incomplete paired-year coverage in regional delay cards and CSV.

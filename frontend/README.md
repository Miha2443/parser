# Moscow Analytics Dashboard

Просмотр на текущей машине: http://localhost:5173.
Утверждённое оформление сохранено; профиль подключён к API и полному экспорту Excel.
Дорожная карта: `../docs/dashboard-roadmap.md`.

React + TypeScript + Vite, ECharts canvas charts and Lucide controls. API is the
default data mode. All dashboard sections run locally in React: developer profile,
apartments, sales, annual/operational/linear commissioning, construction, salary,
consumer prices, national accounts, map, home, updates and TDM. Streamlit remains
available separately for comparison and rollback. Analytics never change input
files; TDM sends only after an explicit user action.

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
All sidebar sections now use local React routes. The profile retains an explicit
comparison link to Streamlit at `localhost:8501` in a separate tab.

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

## Operational And Linear Commissioning

`/commissioning/operational` and `/commissioning/linear` reuse the approved shell.
Both use live API catalogs and reports only, with no frozen fallback. Operational
reads `/api/v1/commissioning/operational/catalog` and its report; linear reads
`/api/v1/linear/catalog` and its report. URL filters remain separate on each route,
including history, reload and menu navigation. Operational uses `month`,
`exclude_mkd`, `year`, `quarter`, `cumulative`; linear uses `year`, `quarter`,
`cumulative`, `indicator`. Defaults and available options come from the catalog.
Linear defaults to the catalog's latest complete quarter, not an incomplete Q4.
Changing linear year selects that year's catalog default quarter; operational
year preserves its compatible quarter. Controls remain available during loading
and errors, and changing filters immediately hides and aborts stale report data.
Both 404 and version refreshes are bounded per catalog generation/filter pair,
allowing another refresh after later generations. Persistent errors stay visible
with retry. No API failures are disguised as empty reports or demo data.

Operational renders all supplied years (16 on the current live report) in two
stacked charts. Period/remainder/totals/growth come from the backend's wrapped
`{value}` points without recomputation or unit conversion. The backend's legacy
plotting zero fills stay zero, while missing history remains null/dashes in the
full tables and original-value chart CSV column. Segment hover shows totals,
growth and missing source-period values. Wide charts show total/period/growth
labels; compact charts keep all bars and values in tooltips/data tables without
internal scrolling. Annual tables use two decimals for million m2, one for
growth; CSV retains complete original precision. The quarter structure groups
all 15 supplied metrics into common/housing/standalone nonresidential groups,
without calculating or reconciling overlapping parent totals.

Linear shows all four indicators with independent horizontal plan/fact bars,
one decimal for km and zero for other source units; completion uses one decimal.
Missing fact remains null/dash and never becomes a zero bar. Actual zero facts
stay zero. Selected-indicator quarter trends use grouped bars; quarter/cumulative
values and percentages are backend results, not frontend sums. The complete
all-quarter table retains code, indicator, unit, year, quarter, plan, fact and
completion, including missing future facts and every API row.

Both pages provide chart CSV/PNG and full table CSV regardless of search/page or
hidden series. Operational tables use `operationalhousingSearch/Page/Size` and
`operationalnonresSearch/Page/Size`; linear uses `linearAllSearch/Page/Size`.
Each suffix forms a separate URL parameter. Excel always sends the complete
report selection plus displayed `required_version` to its `/export` route;
table filters never enter the request. Missing version is rejected by the API.
HTTP 409 downloads nothing and asks for reload; scope changes cancel pending
downloads. Operational candidate file dates are explicitly not proof of selected
inputs; reported monthly-history file/date are shown separately. Linear identifies
its selected report file/date. Source issues, notes, date evidence and generation
metadata are retained and visible without a fabricated freshness claim.

Run `node --test src/commissioning.test.mjs` and
`node src/commissioning.browser.cjs http://127.0.0.1:5173`. Set `NODE_PATH` as above
for Playwright; `DASHBOARD_CHECK_LIVE=1` adds operational exclusion/cumulative
combinations and all linear quarters in both modes, real CSV/Excel downloads,
both themes and 360/390/430/768/1280/1920 layouts. Fixtures cover nulls/zeros,
all structure values, precision, catalog defaults, URL history/navigation,
scope/abort, successive generations, generation-bounded 404s, errors/retry,
removed options, empty data/catalogs, full exports/409, every available series
hover, canvas pixels, chart scrolling and cropped axis labels. Screenshots and
`checks.json` go to `%TEMP%/dashboard-commissioning-checks`, overridden with
`COMMISSIONING_OUTPUT`. Calculation parity and workbook content remain backend
checks; these frontend checks verify API preservation and browser behavior.

## Annual Commissioning And Current Construction

Local routes `/commissioning/annual` and `/construction` use only the live
versioned API. `/` remains the existing developer profile. The parent-owned
map and service pages are integrated at `/map`, `/home`, `/updates`, and `/tdm`.

- Annual catalog/report: `/api/v1/commissioning/annual/catalog` and
  `/api/v1/commissioning/annual?region=msk|rf`. The page preserves all seven
  Moscow or two RF charts, units in million square meters, source series colors,
  historical overrides, all supplied year rows, and both supplied period summaries.
  Legacy zero-filled annual columns remain zero with the backend's limitations
  displayed; the RF 2026 zero is explicitly not a confirmed zero annual result.
- Construction catalog/report: `/api/v1/construction/catalog` and
  `/api/v1/construction?region=msk|rf&permit_kind=total|housing|nonresidential&month=N`.
  Latest available month defaults per permit kind. Changing kind preserves a
  compatible month. KPI region never changes the Moscow-only permit scope.
  Construction, readiness, and detailed sales period labels remain independent.
  KPI units/precision are supplied by the API. Detailed sales source areas in
  thousand square meters are displayed in million square meters by division by
  1,000, with raw values retained in the complete CSV/table.
- Missing values remain dashes, source zero values remain zero, and missing sales
  percentages do not become zero-valued donuts. Permit charts use the backend's
  zero-filled plotting records; the raw table and chart CSV preserve missing
  source values separately. Supplied growth and annual summaries are not recalculated.
- URL `region`, `permit_kind`, `month` and per-table `annual<ID>Search/Page/Size`,
  `constructionSalesSearch/Page/Size`, `constructionPermitsSearch/Page/Size`
  restore through navigation, back/forward, and reload. Full CSV exports ignore
  table search/pagination. Every chart supports full CSV and PNG. Excel always
  sends `required_version`; HTTP 409 is visible and does not download a workbook.
- Requests and downloads cancel when their scope changes. Catalog reconciliation
  on 404/version mismatch is bounded per filter and catalog generation, including
  repeated sequential changes. API/schema failures never substitute demo data.
  Candidate source dates and files are labelled as candidate evidence, with
  separately selected operational-file metadata preserved.

Verification commands (from `frontend`, same bundled `NODE_PATH` as above):

```powershell
node --test tests/*.test.mjs src/*.test.mjs
$env:DASHBOARD_CHECK_LIVE='1'
node src/annualConstruction.browser.cjs http://127.0.0.1:5173
```

Browser screenshots and `checks.json` are saved under
`$env:TEMP/dashboard-annual-construction-checks` (override with
`ANNUAL_CONSTRUCTION_OUTPUT`). This script covers contract fixtures, live
regions/kinds, version-pinned XLSX, full CSV/PNG, URL history, failures/retry,
stale cancellation, sequential generations, bounded 404 recovery, missing
values, both themes, responsive overflow, canvas pixels and physical series hover.

Verified locally on 2026-10-08: operational/linear passed 50 fixture/live layouts,
96 hover targets and 12 live slices with 12 pinned XLSX downloads. Annual/current
construction passed 72 layouts, 232 hover targets, 20 chart CSV/PNG downloads,
eight live selections and eight pinned XLSX downloads. The complete frontend
unit suite and TypeScript check pass. Production build splits the map engine
into the lazy `MapPage` chunk; it is not part of the initial non-map JS bundle.

## Map Configuration And Deployment

`/map` uses MapLibre with a configurable XYZ raster basemap. The default
`https://tile.openstreetmap.org/{z}/{x}/{y}.png` is for limited, interactive local
preview, not a production hosting commitment. OSM tiles have limited capacity
and no availability guarantee. Follow the
[OSMF tile usage policy](https://operations.osmfoundation.org/policies/tiles/):
keep attribution visible, preserve browser identification/referrer and normal
HTTP caching, and do not bulk-download or prefetch tiles. Automated browser
checks use fixture tiles rather than fetching public OSM tiles.

The existing `.env.example` documents the provider settings. Set both in
`frontend/.env.local` for an approved provider or your own XYZ tile service:

```dotenv
VITE_MAP_TILE_URL=https://your-tile-server.example/{z}/{x}/{y}.png
VITE_MAP_ATTRIBUTION=Your provider attribution
```

Restart Vite after development configuration changes; rebuild for production.
All `VITE_*` values are public browser configuration, so never put private
credentials here. Preserve the chosen provider's required attribution and use
only tiles whose terms permit the intended deployment, traffic and testing.
Production must use your own service or a provider with appropriate permission
and capacity; the default OSM endpoint is not the production plan.

`/home`, `/updates` and `/tdm` are local React routes backed by the local API;
`/` still opens the developer profile. TDM is intended for a trusted localhost
deployment only. This migration does not provide public authentication or
authorization. Do not expose the TDM page/API as a public sending service.

## Economics Pages

`/economics/salary`, `/economics/ipc` and `/economics/accounts` use the matching
`/api/v1/economics/{family}` endpoints. Their catalog supplies all regions,
industries, periods and defaults. Year/quarter/month, cumulative salary and IPC
base controls retain the original calculation rules. National accounts keep
four independent regional selections, structure region/mode/industries and
industry-index region/industries/total controls. Empty multiselections remain
empty, rather than silently restoring defaults.

Filters persist in URL and API responses are versioned. Stale requests are
aborted, failed requests display retry, and no frozen fallback is substituted.
Tables preserve original values and nulls; search/pagination affect display
only. CSV and version-pinned Excel retain complete selected data. Each bar
segment and line point has its own unit-aware hover; all charts support PNG.
Sources distinguish file dates from recorded download dates.

Verification: `node src/economics.browser.cjs` exercises isolated fixture and
live API cases (set `DASHBOARD_CHECK_LIVE=1` for live cases). Screenshots/reports
are stored in `%TEMP%/dashboard-economics-checks`. The final integrated
production check is `node src/dashboard.browser.cjs`, default port 5180;
override with `DASHBOARD_TEST_URL`. It verifies 14 routes in both themes at
360/1280 px, chart pixels/overflow and local sidebar navigation. Map tiles and
TDM sends are covered separately with fixtures, not public provider requests
or real outbound messages.

## Chart Layout And Data Disclosures

The home page groups real-estate navigation into current construction,
commissioning, and developer profile. Expandable sidebar groups use larger,
bold headings distinct from page links.

Chart-only controls sit next to their chart. Operational commissioning retains
independent `housingMonth` and `nonresMonth` URL selections; its quarterly
structure uses only year, quarter, and cumulative mode. All twelve months are
selectable, but missing source observations remain missing. Quarterly structure
and previous-year comparisons use the API values, not presentation examples.

Chart data and segment tables start collapsed. Economics raw/pivot tables share
the chart's data disclosure rather than duplicating it. CSV retains complete
source rows. Forecast charts omit only all-null year buckets and separate area
from percentages; index charts use an observed-range axis without changing data.
Apartment room strips expose source percentages on hover or keyboard focus.

`node src/design-polish.browser.cjs` checks 48 live layouts across dark/light
themes and 360/1280/1920 px, independent filters, collapsed data, exports, and
room-strip tooltips. It defaults to port 5180; set `DASHBOARD_TEST_URL` to change
the URL. Screenshots and results are in `%TEMP%/dashboard-design-polish`.
This batch passed 89 frontend unit tests and 105 backend tests.

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

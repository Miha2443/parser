# Developer Dashboard: Visual Approval

Просмотр на текущей машине: http://localhost:5173.
Визуальный этап готов к согласованию; API и Excel подключаются после утверждения.
Дорожная карта: `../docs/dashboard-roadmap.md`.

React + TypeScript + Vite, ECharts canvas charts and Lucide controls. This stage
reads `public/profile-snapshot.json` only. It does not implement an API or Excel
export, and it does not replace the other Streamlit pages.

## Run

- `pnpm install` (or `npm install`)
- `pnpm dev` (or `npm run dev`), normally `http://127.0.0.1:5173`
- `pnpm build` / `pnpm typecheck`
- `pnpm test` (Node 24, native TypeScript type stripping)

On this workspace host, pnpm is bundled at
`C:/Users/Mihail/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm.cmd`.
From the repository root, `scripts/start_dashboard_preview.ps1` also launches the
preview after dependencies are installed and chooses a free port. The current
verified session was started directly with Vite; the launcher was syntax-checked.

The frozen snapshot is produced and owned by the baseline worker. Do not edit
it in the frontend. Contract: `../docs/dashboard-baseline.md`, schema version 1.

## Behavior

The first page is the actual developer profile. Developer, apartment region and
annual chart year bounds persist in URL parameters `developer`, `region`,
`from`, `to`, including browser back/forward. Regions and years come from the
snapshot. Apartment region does not affect Moscow monitoring, ratings, sales,
delays or escrow; every section names its own scope.

Dark is the default theme; theme, sidebar width and each nested navigation
expansion persist locally. Other sidebar pages open the existing Streamlit
server at `localhost:8501` in a separate tab. Its implicit routes strip the
numeric filename prefixes.

Each chart offers segment hover, clickable swatch legends, a collapsible data
table, CSV and PNG. Apartments, delays and full object registers export CSV.
Object registers support search and pagination; export includes every source
row. No values are generated or substituted by this UI. Missing values remain
null/dashes. Delay display fields intentionally preserve the original page's
dash semantics and uncertain reporting-period warnings.

Source dates are the snapshot's original metadata dates, not a claim of live
freshness. Source details distinguish the generation date and source-file
modification metadata. Underlying opened paths are retained.

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
coordinated by the parent task through `scripts/check_dashboard_frontend.cjs`.

Approval is still a human decision. Backend/API and Excel implementation remain
outside this visual stage.

The source snapshot retains original display captions for baseline verification.
The frontend labels the first input donut with its actual covered years and
discloses incomplete paired-year coverage in regional delay cards and CSV.

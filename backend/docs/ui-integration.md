# UI integration verification

The frontend uses FastAPI OpenAPI via openapi-typescript and openapi-fetch. No manual API response interfaces remain in `src/lib/api.ts`. Generated files are checked in; `npm run api:sync` exports/generates/compiles/tests and `npm run api:check` compares against temporary regenerated files before compiling/testing. A stale artifact was deliberately introduced and rejected, then restored. CI runs the same check and browser integration.

## Connected pages

- Dashboard: aggregate totals/severity/scanner counts, recent scans and critical findings.
- Module catalog/detail: persisted repositories and latest scan.
- Scan form: POST, navigation to durable scan ID, cancellable 1.5-second polling, stage status.
- Scan result: summary, scanner-filtered findings, dependencies, bounded SVG graph, entities, flows, neighbor flows, trace, correlations/detail/evidence path.
- History: persisted scans, status filter and pagination.
- System status: API/DB/Checkov/static engine availability.
- Development verification: real endpoint checks, URL preset, fixture demonstration, trace inputs.

## Fixture provenance and scope

Development route is registered only when `APP_ENVIRONMENT=development`. It has no arbitrary filesystem/source parameter. A fixed packaged HCL fixture uses SourceCollector/SecurityScanner adapters and the normal ScanService, parser, normalizer, graph builders, correlation engine and repositories. The Checkov input is a controlled fixture, not a real scanner invocation. Module URL is `fixture://correlation/iam`; generated module `is_demo` and finding `raw_data.development_fixture` preserve provenance without a DB migration. UI labels the scenario. SourceType retains the existing storage enum; fixture provenance takes precedence over its legacy GITHUB storage value.

Production defaults hide the development UI and omit its API route. The exported OpenAPI intentionally includes development routes as a superset. Production non-registration is separately tested. Demo scans contribute to dashboard aggregates. Local Compose listens only on loopback; public deployment still requires authentication/access controls.

## Actual browser verification — 2026-10-11

Chromium against Docker Compose frontend + FastAPI + PostgreSQL:

- Deterministic scan form routes collector entry to the actual demo pipeline (no hardcoded frontend response); checks six tabs, graph, trace, TFCR001 CRITICAL / MEDIUM confidence, evidence/attack path, dashboard/module/history.
- Development screen checks health, preset does not submit, demo starts, six API checks succeed.
- Network failure is shown with retry in UI.
- Live public GitHub scan is started by the browser form without fixture interception.

Live repository: `https://github.com/terraform-aws-modules/terraform-aws-vpc`

- Scan: `5cf73b28-6029-4ee9-be6b-ae5b6ba7e2db`
- Commit: `b3abd6df2ecf052451a361ed55b8f06f8742a795`
- Duration: 77,738 ms
- Entities: 481
- Dependency edges: 1,466
- Data flow edges: 947
- Cross-module / opaque / unresolved flow counts: 0 / 0 / 0
- Checkov: 46; Source Trust: 1; Correlation: 0
- PARTIAL analysis is shown independently of overall COMPLETED status.

Correlation 0 is expected here; the UI explains that coexisting findings without relationship evidence are not combined. Development trace: `module.policy_source.policy → local.policy → aws_iam_role_policy.admin.policy`.

## Limits

Graph rendering is limited to 20/40/60 selected nodes; backend graph payload is still complete. Trace remains bounded by backend depth/path/traversal limits. Source/sink metadata and evidence dictionaries are rendered safely as text (no HTML injection). Dynamic expressions and opaque dependencies retain backend limitations. Trusted score and module certification are not implemented; the previous configurator is preserved only as an explicitly development-only illustrative template.

## Final regression — 2026-10-12

- Full backend suite with PostgreSQL: **273 passed** (includes migration upgrade/downgrade/re-upgrade and real Checkov fixtures).
- Frontend client/contract/error/abort/timeout tests: **11 passed**.
- OpenAPI sync/check and TypeScript compile: passed; stale-file negative check passed.
- Ruff check and formatter: passed. New/modified integration TypeScript files pass Prettier.
- Production and development Docker frontend/API builds: passed.
- Production HTTP checks: `/dev/analysis-test` = 404; POST `/api/dev/demo/correlation` = 404.
- Chromium live+fixture integration: **4 passed**, including real VPC clone/Checkov scan.
- Module search was added as an additive `GET /api/modules?search=...` parameter; wildcard characters are escaped, search is paginated, and the OpenAPI/TS client artifacts were synchronized.
- No database migration required. `is_demo` is a computed response field; scanner breakdown and demo finding provenance use existing JSON data.

The GitHub Actions workflows are provided as files and locally equivalent commands were run; remote CI execution requires pushing this workspace to a repository. This workspace itself does not contain `.git`.

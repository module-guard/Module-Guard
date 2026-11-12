# Live smoke (issue #49)

`.github/workflows/live-smoke.yml` runs the paths PR CI cannot: real GitHub and Terraform Registry
collection, real Checkov, on the PostgreSQL stack of `docker-compose.e2e.yml`. PR CI (`ci.yml`)
does not run it.

## When it runs

| Trigger | What runs |
|---|---|
| Weekly, Sunday 18:00 UTC (`schedule`) | live specs (`RUN_LIVE_SCAN=true`) |
| Actions tab → Run workflow | `live` (default on) and/or `demo` |

Live specs: `mirror`, `platform`, `use`, `integrity` (`integrity.spec.ts` holds the
collection → analysis → verified download flow for GitHub and Registry on PostgreSQL).

## Skips are checked

`.github/scripts/require-no-new-skips.py` reads the Playwright JSON report and fails when a test
outside `.github/scripts/live-smoke-allowed-skips.txt` was skipped, or fewer than 9 passed
(9 is what a real local run measured; raise it when a live test is added), or the report has no
tests, or the report cannot be read (Playwright died before writing it).
Allowed: the Terraform steps (Terraform is removed from the runner, AGENTS.md rule 7) and the two
audits that need a hand-prepared `GRAPH_AUDIT_SCAN_ID`. Add a line there only with a reason.
Self-check: `python .github/scripts/test_require_no_new_skips.py`.

## Failure notification

On a scheduled run that fails, the `notify` job opens one issue labelled `live-smoke-failure`
(`issues: write` with the built-in token, no secret); later failures comment on it. Close the issue
when fixed. Manual runs do not notify; you are watching them.

## GitHub token and rate limits

No secret is used. The collectors call GitHub unauthenticated (no `Authorization` header in
`backend/app`), so the 60 requests/hour per IP limit applies and runners share IPs. A 403/429 from
GitHub fails the run. Mitigation today: weekly cadence and few modules. Passing `GITHUB_TOKEN` to the
API container needs a backend change (request to the collector owner), not made here.

## Flaky tests and retries

The live run uses `--retries=0` on purpose: a retry would hide a flaky external dependency. Record a
flaky failure by commenting on the open `live-smoke-failure` issue (or a new one) with the run URL and
spec; two failures on the same spec within a month means fix or remove it. PR CI keeps its own retry policy.

## What has been verified, and what has not

**Verified locally (2026-11-12), not on GitHub:**

| Check | Result |
|---|---|
| `live-smoke.yml` and `ci.yml` parse; `actionlint` 1.7.7 (checksum-verified download) | 0 findings; shellcheck was not installed, so the `run:` scripts were not shell-linted |
| Triggers: weekly cron + `workflow_dispatch` (`live` default on, `demo` off); `inputs` are empty on a schedule so the expression falls back to live | read from the parsed file; not exercised by GitHub |
| Permissions: workflow `contents: read`; only `notify` has `issues: write`; checkout uses `persist-credentials: false` | read from the file |
| No `secrets.*`, `set -x`, debug flags or echoed tokens; the only token (`github.token`) is in `notify`, whose issue text is the run URL only | grep over the workflow and the four live specs |
| `notify` logic against a fake `gh`: no issue → create; open issue → comment; only a closed issue → create; closed + open → comment on the open one; label already existing is ignored; token never printed | all four cases behaved as intended (real `gh` semantics were not tested) |
| Skip guard against a **real Playwright JSON report** plus fixtures: unexpected skip, allowed skip, too few passes, no tests, missing file, invalid JSON, stale allow-list line | `python .github/scripts/test_require_no_new_skips.py` → ok |
| Live specs on the real `docker-compose.e2e.yml` stack (PostgreSQL, real Checkov, real github.com and Registry), Terraform kept off `PATH`, `--retries=0` | run 1: 8 passed, **1 failed**, 5 skipped; after fixing the spec: **9 passed, 0 failed, 5 skipped** (all skips on the allow-list), guard exit 0, about 5 minutes |
| Conflicts with `ci.yml` | none: different triggers, concurrency groups (`live-smoke` vs `ci-<ref>`) and job names. Both use the compose project `module-guard-e2e` and ports 8001/3100, which only matters on a shared (self-hosted or local) machine |

The failure in run 1 was real drift: `mirror.spec.ts` looked for the 미러 label inside the card's title
link, but the card is an `<article>` and the label sits outside the link. PR CI never saw it because that
spec skips there. The locator now selects the surrounding `article`; the assertions are unchanged.

**Not verified (needs a real GitHub Actions run):**

- that GitHub accepts and schedules the workflow, and that a scheduled run behaves like the local run
- the `notify` job on a real failed scheduled run (it only runs on `schedule`), including label creation
  and the issue/comment permissions with the built-in token
- the guard step on the runner (Python version, report path) and the artifact upload
- GitHub's unauthenticated rate limit on shared runner IPs, and the runner's real duration

**What a real run needs:** the workflow merged to the default branch (scheduled runs and the Run workflow
button only exist there); someone with permission to push that merge and to run workflows; Actions enabled
for the repository, with `issues: write` allowed for the workflow token; a runner with Docker and about 5
minutes (the job allows 120). Scheduled workflows are paused by GitHub after 60 days without repository
activity. Until one real manual run and one real failure notification are on record, the completion
criteria of issue #49 that depend on them stay open.

**Check after the first real run:** passes should be 9 with the same 5 allow-listed skips; if the count
differs, adjust the minimum in the workflow and in this file.

## Run it locally

Keep Terraform off `PATH` (AGENTS.md rule 7): the specs run `terraform init` whenever `terraform` is found,
so on a machine that has it, start from a `PATH` without it (`command -v terraform` must print nothing).

```bash
python .github/scripts/test_require_no_new_skips.py          # guard self-check
docker compose -p module-guard-e2e -f docker-compose.e2e.yml up -d --build --wait
npm ci && npx playwright install chromium
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 NEXT_PUBLIC_ENABLE_TEST_UI=true NEXT_DIST_DIR=.next-e2e NEXT_OUTPUT=server npx next build
NEXT_DIST_DIR=.next-e2e NEXT_OUTPUT=server npx next start -p 3100 &
RUN_LIVE_SCAN=true PLAYWRIGHT_BASE_URL=http://localhost:3100 PLAYWRIGHT_API_URL=http://localhost:8001 \
  PLAYWRIGHT_JSON_OUTPUT_NAME=test-results/report.json \
  npx playwright test e2e/mirror.spec.ts e2e/platform.spec.ts e2e/use.spec.ts e2e/integrity.spec.ts \
  --retries=0 --reporter=list,json
python .github/scripts/require-no-new-skips.py test-results/report.json .github/scripts/live-smoke-allowed-skips.txt 9
docker compose -p module-guard-e2e -f docker-compose.e2e.yml down -v
```

"""Reputation cohort tooling for issue #52 (docs/reputation-cohort-report.md).

    python scripts/reputation_cohort.py validate   # offline: stratification, split, digests
    python scripts/reputation_cohort.py analyze    # offline: JSON metrics from the snapshots
    GITHUB_TOKEN=... python scripts/reputation_cohort.py collect [--force]   # network

`validate` and `analyze` read only docs/reputation-cohort.json and the committed snapshots in
docs/reputation-cohort-evidence/, so every number in the report is reproducible offline.
`collect` stores exactly the inputs reputation_service.evaluate() receives (allowlisted public
fields, manifest paths at the pinned commit) and never modifies reviewer fields.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.reputation_service import POLICY, evaluate, github_repo, registry_module

DOCS = Path(__file__).resolve().parents[1] / "docs"
COHORT = DOCS / "reputation-cohort.json"
EVIDENCE = DOCS / "reputation-cohort-evidence"

PROVIDERS = ("aws", "azurerm", "google", "kubernetes", "other")
SIZES = ("small", "large")
STATES = ("new", "active", "archived")
SPLITS = ("calibration", "evaluation")
# Stratum rules (documented in reputation-review-guide.md); the clean gap 21-49 is excluded.
SMALL_MAX_TF, LARGE_MIN_TF = 20, 50
NEW_DAYS, ACTIVE_PUSH_DAYS = 365, 180
STATUSES = ("confirmed", "insufficient", "unverified", "failed", "not_applicable", "stale")
REASON_CODES = (
    "NO_MANIFEST",
    "NOT_GITHUB_SOURCE",
    "NOT_REGISTRY_SOURCE",
    "FIELD_ABSENT",
    "RATE_LIMITED",
    "NOT_FOUND",
    "NOT_PUBLIC",
    "SOURCE_UNAVAILABLE",
    "OTHER",
)
FILE_METRICS = ("security_policy_file", "readme_file", "test_files")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
GITHUB_FIELDS = ("archived", "pushed_at", "created_at", "stargazers_count")
REGISTRY_FIELDS = ("published_at", "downloads")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(snapshot):
    """Hash the re-serialized evidence (not file bytes) so line endings cannot change it."""
    body = {k: snapshot[k] for k in ("module_url", "policy_version", "collected_at", "commit")}
    body["evidence"] = snapshot["evidence"]
    return "sha256:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def parse_time(text):
    return datetime.fromisoformat(text)


def classify(facts, collected_at):
    """(size, state) from stratum facts; None where the rules leave a gap."""
    tf, now = facts["tf_count"], parse_time(collected_at)
    size = "small" if tf <= SMALL_MAX_TF else "large" if tf >= LARGE_MIN_TF else None
    if facts["archived"]:
        state = "archived"
    elif parse_time(facts["created_at"]) > now - timedelta(days=NEW_DAYS):
        state = "new"
    elif parse_time(facts["pushed_at"]) >= now - timedelta(days=ACTIVE_PUSH_DAYS):
        state = "active"
    else:
        state = None
    return size, state


def inputs_of(snapshot):
    ev = snapshot["evidence"]
    return {
        "version_id": uuid5(NAMESPACE_URL, snapshot["module_url"]),
        "manifest": {"files": [{"path": p} for p in ev["files"]]},
        "source": ev["source"],
        "commit": ev["commit"],
        "at": parse_time(snapshot["collected_at"]),
        "github": copy.deepcopy(ev["github"]),
        "github_error": ev["github_error"],
        "registry": copy.deepcopy(ev["registry"]),
        "registry_error": ev["registry_error"],
    }


def run_evaluate(inputs):
    return evaluate(
        inputs["version_id"],
        inputs["manifest"],
        inputs["source"],
        inputs["commit"],
        inputs["at"],
        github=inputs["github"],
        github_error=inputs["github_error"],
        registry=inputs["registry"],
        registry_error=inputs["registry_error"],
    )


def popularity_variants(inputs):
    """Copies with stars/downloads set to 0 and to a huge value; only existing int fields change."""
    variants = []
    for value in (0, 10**9):
        changed, touched = copy.deepcopy(inputs), False
        for source, key in (("github", "stargazers_count"), ("registry", "downloads")):
            if changed[source] and isinstance(changed[source].get(key), int):
                changed[source][key], touched = value, True
        if touched:
            variants.append(changed)
    return variants


def non_popularity(evaluation):
    return [m.model_dump() for m in evaluation.metrics if m.axis != "popularity"]


def cohens_kappa(a, b):
    """Cohen's kappa for two raters; None when undefined (no pairs or expected agreement 1)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    return None if expected == 1 else round((observed - expected) / (1 - expected), 4)


def disagreement_rate(a, b):
    if not a or len(a) != len(b):
        return None
    return round(sum(x != y for x, y in zip(a, b, strict=True)) / len(a) * 100, 2)


def load_cohort():
    return json.loads(COHORT.read_text(encoding="utf-8"))


def load_snapshot(slot):
    path = EVIDENCE / f"slot-{slot:02d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def repo_key(snapshot):
    return snapshot["stratum_facts"]["repo"].lower()


def validate(cohort, snapshots):
    """Return a list of problems; empty means the cohort is complete and consistent."""
    errors, samples = [], cohort.get("samples", [])
    if [s.get("slot") for s in samples] != list(range(1, 31)):
        errors.append("slots must be exactly 1..30 in order")
    strata = [(s.get("provider"), s.get("size"), s.get("state")) for s in samples]
    expected = {(p, z, t) for p in PROVIDERS for z in SIZES for t in STATES}
    if sorted(strata) != sorted(expected):
        errors.append("strata must cover provider x size x state exactly once")
    if sorted(s.get("split") for s in samples) != sorted(SPLITS[0:1] * 15 + SPLITS[1:] * 15):
        errors.append("split must be calibration x15 and evaluation x15")
    projects = {}
    for s in samples:
        n, snap = s.get("slot"), snapshots.get(s.get("slot"))
        url = s.get("module_url")
        if not url or not (github_repo(url) or registry_module(url)):
            errors.append(f"slot {n}: module_url missing or not a public github/registry URL")
        if not snap:
            errors.append(f"slot {n}: evidence snapshot missing")
            continue
        if snap["module_url"] != url or snap["slot"] != n:
            errors.append(f"slot {n}: snapshot does not belong to this slot")
        if snap["policy_version"] != POLICY:
            errors.append(f"slot {n}: snapshot policy {snap['policy_version']} != {POLICY}")
        if not SHA256.fullmatch(str(s.get("evidence_digest"))):
            errors.append(f"slot {n}: evidence_digest format")
        elif digest(snap) != s["evidence_digest"]:
            errors.append(f"slot {n}: evidence_digest does not match snapshot")
        if (s.get("commit"), s.get("tag")) != (snap["commit"], snap["tag"]):
            errors.append(f"slot {n}: commit/tag differ from snapshot")
        key = repo_key(snap)
        if github_repo(url) and github_repo(url).lower() != key:
            errors.append(f"slot {n}: stratum repo {key} differs from module_url")
        if key in projects:
            errors.append(f"slot {n}: project {key} duplicates slot {projects[key]}")
        projects[key] = n
        size_state = classify(snap["stratum_facts"], snap["collected_at"])
        if size_state != (s.get("size"), s.get("state")):
            errors.append(
                f"slot {n}: facts give {size_state}, plan says {s.get('size')}/{s.get('state')}"
            )
        name = key.split("/")[1]
        named = [p for p in PROVIDERS[:4] if p in name]
        if (s.get("provider") == "other") == bool(named) or (
            named and named[0] != s.get("provider")
        ):
            errors.append(f"slot {n}: provider {s.get('provider')} does not match repository name")
        for who in ("reviewer_a", "reviewer_b"):
            for metric, item in (s.get(who) or {}).items():
                ok = item.get("status") in STATUSES and item.get("reason") in (*REASON_CODES, None)
                if not ok:
                    errors.append(f"slot {n}: {who}.{metric} has an unknown status or reason code")
    return errors


def group_stats(rows):
    """Missing / not-applicable rates and mean coverage for evaluated slots (popularity excluded)."""
    metrics = [m for r in rows for m in r["evaluation"].metrics if m.axis != "popularity"]
    applicable = [m for m in metrics if m.status != "not_applicable"]
    axes = rows[0]["evaluation"].coverage if rows else {}
    return {
        "slots": len(rows),
        "missing_rate_pct": round(
            sum(m.status in ("insufficient", "unverified", "failed") for m in applicable)
            / len(applicable)
            * 100,
            2,
        )
        if applicable
        else None,
        "not_applicable_rate_pct": round(
            sum(m.status == "not_applicable" for m in metrics) / len(metrics) * 100, 2
        )
        if metrics
        else None,
        "mean_coverage_pct": {
            axis: round(sum(r["evaluation"].coverage[axis] for r in rows) / len(rows), 2)
            for axis in axes
        },
        "file_absent_pct": {
            name: round(
                sum(
                    next(m for m in r["evaluation"].metrics if m.id == name).value is False
                    for r in rows
                )
                / len(rows)
                * 100,
                2,
            )
            for name in FILE_METRICS
        },
    }


def reviewer_stats(samples, rows):
    axis_of = {m.id: m.axis for r in rows for m in r["evaluation"].metrics}
    pairs = {}
    for s in samples:
        a, b = s.get("reviewer_a"), s.get("reviewer_b")
        if not a or not b:
            continue
        for metric in sorted(set(a) & set(b)):
            pairs.setdefault(axis_of.get(metric, "unknown"), []).append(
                (a[metric]["status"], b[metric]["status"])
            )
    if not pairs:
        return {"status": "pending", "reason": "no slot has both reviewer_a and reviewer_b"}
    return {
        "status": "computed",
        "axes": {
            axis: {
                "pairs": len(p),
                "disagreement_pct": disagreement_rate([x for x, _ in p], [y for _, y in p]),
                "kappa": cohens_kappa([x for x, _ in p], [y for _, y in p]),
            }
            for axis, p in sorted(pairs.items())
        },
    }


def analyze(cohort, snapshots):
    samples = cohort["samples"]
    rows = []
    for s in samples:
        snap = snapshots[s["slot"]]
        inputs = inputs_of(snap)
        rows.append(
            {"sample": s, "snap": snap, "inputs": inputs, "evaluation": run_evaluate(inputs)}
        )
    changed, applicable = [], 0
    for r in rows:
        variants = popularity_variants(r["inputs"])
        applicable += bool(variants)
        for v in variants:
            e = run_evaluate(v)
            if e.coverage != r["evaluation"].coverage or non_popularity(e) != non_popularity(
                r["evaluation"]
            ):
                changed.append(r["sample"]["slot"])
    deterministic = all(
        run_evaluate(inputs_of(r["snap"])) == r["evaluation"]
        and digest(r["snap"]) == r["sample"]["evidence_digest"]
        for r in rows
    )
    forms = {"github": [], "registry": []}
    for r in rows:
        forms["registry" if registry_module(r["snap"]["module_url"]) else "github"].append(r)
    zero_without_basis = [
        {"slot": r["sample"]["slot"], "axis": m.axis}
        for r in rows
        for m in {m.axis: m for m in r["evaluation"].metrics}.values()
        if r["evaluation"].coverage.get(m.axis) == 0
        and all(x.status == "not_applicable" for x in r["evaluation"].metrics if x.axis == m.axis)
    ]
    return {
        "policy_version": POLICY,
        "composition": {
            key: {
                v: sum(s[key] == v for s in samples) for v in dict.fromkeys(s[key] for s in samples)
            }
            for key in ("provider", "size", "state", "split")
        },
        "source_form": {k: len(v) for k, v in forms.items()},
        "by_state": {
            t: group_stats([r for r in rows if r["sample"]["state"] == t]) for t in STATES
        },
        "by_size": {z: group_stats([r for r in rows if r["sample"]["size"] == z]) for z in SIZES},
        "by_source_form": {k: group_stats(v) for k, v in forms.items() if v},
        "coverage_zero_without_applicable_metrics": zero_without_basis,
        "popularity_sensitivity": {
            "slots_with_popularity_values": applicable,
            "slots_with_changed_non_popularity_output": changed,
            "invariant_holds": not changed,
            "note": "coverage ignores popularity by construction; this checks for regressions only",
        },
        "determinism": {"same_input_same_output_and_digest": deterministic},
        "reviewers": reviewer_stats(samples, rows),
    }


def http_fetch(client):
    def fetch(url):
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    return fetch


def collect_slot(fetch, slot, module_url, now):
    registry_id, repo, registry, source_url = (
        registry_module(module_url),
        github_repo(module_url),
        None,
        None,
    )
    if registry_id:
        body = fetch(f"https://registry.terraform.io/v1/modules/{registry_id}")
        registry = {k: body[k] for k in REGISTRY_FIELDS if k in body}
        source_url = body["source"]
        repo = github_repo(source_url)
    meta = fetch(f"https://api.github.com/repos/{repo}")
    if meta.get("private") is not False:
        raise ValueError(f"{repo} is not public")
    commit = fetch(f"https://api.github.com/repos/{repo}/branches/{meta['default_branch']}")[
        "commit"
    ]["sha"]
    tree = fetch(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1")
    if tree.get("truncated"):
        raise ValueError(f"{repo} tree is truncated")
    files = sorted(e["path"] for e in tree["tree"] if e["type"] == "blob")
    tags = fetch(f"https://api.github.com/repos/{repo}/tags?per_page=100")
    tag = next((t["name"] for t in tags if t["commit"]["sha"] == commit), None)
    return {
        "slot": slot,
        "module_url": module_url,
        "policy_version": POLICY,
        "collected_at": now,
        "commit": commit,
        "tag": tag,
        "evidence": {
            "source": module_url,
            "commit": commit,
            "files": files,
            # The service only fetches GitHub for github.com sources and Registry for registry ones.
            "github": None if registry_id else {k: meta[k] for k in GITHUB_FIELDS if k in meta},
            "github_error": None,
            "registry": registry,
            "registry_error": None,
        },
        "stratum_facts": {
            "repo": repo,
            "repo_source_url": source_url or module_url,
            "archived": meta["archived"],
            "created_at": meta["created_at"],
            "pushed_at": meta["pushed_at"],
            "tf_count": sum(p.endswith(".tf") for p in files),
            "file_count": len(files),
        },
    }


def collect(force, fetch=None):
    """`fetch(url) -> json` is injectable; the default is plain httpx with optional GITHUB_TOKEN."""
    import httpx

    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "User-Agent": "ModuleGuard-cohort/1",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    }
    cohort = load_cohort()
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    EVIDENCE.mkdir(exist_ok=True)
    client = httpx.Client(timeout=20, follow_redirects=False, trust_env=False, headers=headers)
    with client:
        fetch = fetch or http_fetch(client)
        for s in cohort["samples"]:
            path = EVIDENCE / f"slot-{s['slot']:02d}.json"
            if path.exists() and not force:
                print(f"slot {s['slot']}: exists, skipped (use --force)")
                continue
            snap = collect_slot(fetch, s["slot"], s["module_url"], now)
            path.write_text(
                json.dumps(snap, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            s.update(commit=snap["commit"], tag=snap["tag"], evidence_digest=digest(snap))
            print(f"slot {s['slot']}: {snap['stratum_facts']['repo']} {snap['commit'][:10]}")
    cohort["collected_at"], cohort["policy_version"] = now, POLICY
    COHORT.write_text(
        json.dumps(cohort, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def load_all():
    cohort = load_cohort()
    return cohort, {s["slot"]: load_snapshot(s["slot"]) for s in cohort["samples"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("analyze")
    sub.add_parser("collect").add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "collect":
        collect(args.force)
        return 0
    cohort, snapshots = load_all()
    problems = validate(cohort, {k: v for k, v in snapshots.items() if v})
    if args.command == "validate":
        print("\n".join(problems) or "OK: 30 slots, strata, split, projects and digests verified")
        return 1 if problems else 0
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    result = analyze(cohort, snapshots)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    ok = (
        result["popularity_sensitivity"]["invariant_holds"]
        and result["determinism"]["same_input_same_output_and_digest"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

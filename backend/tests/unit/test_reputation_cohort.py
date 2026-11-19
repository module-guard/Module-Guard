"""Offline checks of the #52 cohort: committed snapshots only, no network."""

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "reputation_cohort", BACKEND / "scripts" / "reputation_cohort.py"
)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


@pytest.fixture(scope="module")
def cohort():
    return rc.load_cohort()


@pytest.fixture(scope="module")
def snapshots(cohort):
    return {s["slot"]: rc.load_snapshot(s["slot"]) for s in cohort["samples"]}


def test_committed_cohort_is_complete_and_consistent(cohort, snapshots):
    assert rc.validate(cohort, snapshots) == []


def test_thirty_slots_cover_every_stratum_once(cohort):
    strata = {(s["provider"], s["size"], s["state"]) for s in cohort["samples"]}
    assert len(cohort["samples"]) == 30 and len(strata) == 30
    assert {s["provider"] for s in cohort["samples"]} == set(rc.PROVIDERS)


def test_splits_are_separate_and_projects_unique(cohort, snapshots):
    by_split = {"calibration": set(), "evaluation": set()}
    for s in cohort["samples"]:
        by_split[s["split"]].add(rc.repo_key(snapshots[s["slot"]]))
    assert len(by_split["calibration"]) == len(by_split["evaluation"]) == 15
    assert not by_split["calibration"] & by_split["evaluation"]


def test_digest_format_and_recomputation(cohort, snapshots):
    for s in cohort["samples"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", s["evidence_digest"])
        assert rc.digest(snapshots[s["slot"]]) == s["evidence_digest"]
    changed = copy.deepcopy(snapshots[1])
    changed["evidence"]["files"].append("SECURITY.md")
    assert rc.digest(changed) != cohort["samples"][0]["evidence_digest"]


def test_validate_detects_broken_cohorts(cohort, snapshots):
    bad = copy.deepcopy(cohort)
    bad["samples"][0]["evidence_digest"] = "sha256:" + "0" * 64
    bad["samples"][1]["module_url"] = None
    # same project in both splits, even when one slot is a registry URL
    dup_snaps = copy.deepcopy(snapshots)
    dup_snaps[3]["stratum_facts"]["repo"] = dup_snaps[2]["stratum_facts"]["repo"].upper()
    bad["samples"][5]["reviewer_a"] = {"readme_file": {"status": "maybe", "reason": None}}
    errors = "\n".join(rc.validate(bad, dup_snaps))
    assert "slot 1: evidence_digest does not match" in errors
    assert "slot 2: module_url missing" in errors
    assert "duplicates slot 2" in errors or "duplicates slot 3" in errors
    assert "slot 6: reviewer_a.readme_file has an unknown status" in errors


def test_classify_boundaries():
    facts = {
        "archived": False,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-10-01T00:00:00Z",
    }
    at = "2026-11-19T00:00:00Z"
    assert rc.classify({**facts, "tf_count": 20}, at) == ("small", "active")
    assert rc.classify({**facts, "tf_count": 21}, at) == (None, "active")
    assert rc.classify({**facts, "tf_count": 50}, at) == ("large", "active")
    assert rc.classify({**facts, "tf_count": 5, "archived": True}, at)[1] == "archived"
    assert (
        rc.classify({**facts, "tf_count": 5, "created_at": "2026-01-01T00:00:00Z"}, at)[1] == "new"
    )
    assert rc.classify({**facts, "tf_count": 5, "pushed_at": "2025-01-01T00:00:00Z"}, at)[1] is None


def test_popularity_manipulation_changes_nothing_else(snapshots):
    checked = 0
    for snap in snapshots.values():
        base = rc.run_evaluate(rc.inputs_of(snap))
        for variant in rc.popularity_variants(rc.inputs_of(snap)):
            changed = rc.run_evaluate(variant)
            assert changed.coverage == base.coverage
            assert rc.non_popularity(changed) == rc.non_popularity(base)
            checked += 1
    assert checked >= 30  # every slot has stars or downloads to manipulate


def test_same_snapshot_gives_same_evaluation(snapshots):
    for snap in snapshots.values():
        first = rc.run_evaluate(rc.inputs_of(snap))
        assert first == rc.run_evaluate(rc.inputs_of(copy.deepcopy(snap)))
        assert first.total_score is None


def test_cohens_kappa_fixed_inputs():
    # 50 pairs: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po 0.70, pe 0.50, kappa 0.40
    a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert rc.cohens_kappa(a, b) == 0.4
    assert rc.disagreement_rate(a, b) == 30.0
    assert rc.cohens_kappa(["y", "y"], ["y", "y"]) is None  # expected agreement 1: undefined
    assert rc.cohens_kappa([], []) is None and rc.disagreement_rate([], []) is None


def test_reviewers_pending_until_both_fill_in(cohort, snapshots):
    assert rc.analyze(cohort, snapshots)["reviewers"]["status"] == "pending"
    synthetic = copy.deepcopy(cohort)
    a = ["confirmed", "confirmed", "unverified", "unverified"]
    b = ["confirmed", "unverified", "unverified", "unverified"]
    for sample, x, y in zip(synthetic["samples"], a, b, strict=False):
        sample["reviewer_a"] = {"readme_file": {"status": x, "reason": None}}
        sample["reviewer_b"] = {"readme_file": {"status": y, "reason": "FIELD_ABSENT"}}
    axis = rc.analyze(synthetic, snapshots)["reviewers"]["axes"]["documentation"]
    assert axis == {"pairs": 4, "disagreement_pct": 25.0, "kappa": 0.5}
    assert rc.validate(synthetic, snapshots) == []


def test_report_numbers_are_reproduced_by_the_script(cohort, snapshots):
    report = (BACKEND / "docs" / "reputation-cohort-report.md").read_text(encoding="utf-8")
    block = re.search(r"<!-- analyze:start -->\s*```json\n(.*?)\n```", report, re.S)
    assert block, "report must embed the analyze output"
    assert json.loads(block.group(1)) == json.loads(
        json.dumps(rc.analyze(cohort, snapshots), ensure_ascii=False)
    )

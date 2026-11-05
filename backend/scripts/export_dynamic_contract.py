"""Export schema and synthetic, non-executing examples. Never invokes Terraform."""

import json
from pathlib import Path
from uuid import UUID

from app.schemas.dynamic import DynamicRun

root = Path(__file__).resolve().parents[1]
(root / "docs/dynamic-event.schema.json").write_text(
    json.dumps(DynamicRun.model_json_schema(), indent=2) + "\n"
)
for i, status in enumerate(("success", "failed", "aborted", "timeout", "not_run", "unobservable")):
    run = dict(
        run_id=str(UUID(int=i + 1)),
        input_digest="a" * 64,
        manifest_digest="b" * 64,
        mode="fixture",
        execution_occurred=False,
        status=status,
        environment=dict(
            image_digest="sha256:" + "c" * 64,
            terraform_version="not-executed",
            provider_versions={},
            test_suite_digest="d" * 64,
            mock_version="synthetic-v1",
            policy_version="design-v1",
        ),
        stages=[],
        nodes=[],
        edges=[],
        evidence=[dict(id="fixture", code="FIXTURE_ONLY")],
    )
    for j, name in enumerate(("prepare", "isolation", "test", "observe", "cleanup")):
        state = (
            "not_run"
            if status == "not_run"
            else status
            if name == "test"
            else "unobservable"
            if name == "observe" and status != "success"
            else "success"
        )
        run["stages"].append(
            dict(
                id=name,
                name=name,
                status=state,
                started_at=f"2026-01-01T00:00:{j * 2:02d}Z" if state != "not_run" else None,
                finished_at=f"2026-01-01T00:00:{j * 2 + 1:02d}Z" if state != "not_run" else None,
                evidence_ids=["fixture"],
            )
        )
    if status == "success":
        run["evidence"].append(dict(id="resource", code="SIMULATED_RESOURCE"))
        run["nodes"] = [
            dict(id="input", resource_type="fixture_variable", evidence_ids=["resource"]),
            dict(id="resource", resource_type="fixture_resource", evidence_ids=["resource"]),
        ]
        run["edges"] = [
            dict(
                id="link",
                source="input",
                target="resource",
                relation="simulated_dependency",
                evidence_ids=["resource"],
            )
        ]
    validated = DynamicRun.model_validate(run)
    (root / "app/dynamic/fixtures" / f"{status}.json").write_text(
        validated.model_dump_json(indent=2) + "\n"
    )

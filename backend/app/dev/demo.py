"""Development-only fixture adapters; all analysis uses the normal ScanService."""

import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.collectors.base import CollectedModule, SourceCollector, SourceIdentity
from app.models.enums import SourceType
from app.repositories.module_repository import ModuleRepository
from app.repositories.scan_repository import ScanRepository
from app.scanners.base import ScanResult, SecurityScanner
from app.schemas.scan import ScanAccepted
from app.services.scan_service import ScanService

SOURCE = "fixture://correlation/iam"


class FixtureCollector(SourceCollector):
    def supports(self, source: str) -> bool:
        return source == SOURCE

    def collect(self, source: str, destination: Path) -> CollectedModule:
        if not self.supports(source):
            raise ValueError("Unsupported fixture")
        shutil.copytree(Path(__file__).parent / "fixtures", destination)
        return CollectedModule(
            SourceType.GITHUB, source, destination, "fixture", "development", "fixture", "fixture"
        )


class FixtureScanner(SecurityScanner):
    name = "checkov"

    def scan(self, target_path: Path) -> ScanResult:
        return ScanResult(
            {
                "check_type": "terraform",
                "results": {
                    "failed_checks": [
                        {
                            "check_id": "CKV_AWS_62",
                            "check_name": "IAM administrative privileges (fixture)",
                            "resource": "aws_iam_role_policy.admin",
                            "severity": "HIGH",
                            "file_path": "/main.tf",
                            "check_result": {"result": "FAILED", "evaluated_keys": ["policy"]},
                        }
                    ]
                },
                "summary": {"failed": 1},
            },
            1,
        )


class DemoScanService(ScanService):
    def create(self, source: str = SOURCE) -> ScanAccepted:
        with self.session_factory() as session:
            module = ModuleRepository(session).get_or_create(
                SourceIdentity(
                    SourceType.GITHUB,
                    SOURCE,
                    "development-fixture",
                    "개발용 Fixture 시나리오",
                    "aws",
                )
            )
            scan = ScanRepository(session).create(module.id)
            session.commit()
            return ScanAccepted(scan_id=scan.id)


router = APIRouter()


@router.post("/api/dev/demo/correlation", response_model=ScanAccepted, status_code=202)
async def demo(request: Request):
    service = request.app.state.demo_service
    accepted = await run_in_threadpool(service.create)
    try:
        request.app.state.demo_dispatcher.submit(accepted.scan_id)
    except Exception:
        await run_in_threadpool(service.mark_failed, accepted.scan_id, "Demo queue unavailable.")
        raise
    return accepted

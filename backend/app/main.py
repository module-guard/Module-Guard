import asyncio
import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import SessionDep
from app.api.routes import (
    auth,
    correlations,
    findings,
    flows,
    graphs,
    repositories,
    scans,
    social,
    uploads,
    usage,
)
from app.auth.oauth import GitHubOAuth
from app.core.config import Settings
from app.core.database import create_session_factory
from app.core.exceptions import DomainError
from app.core.logging import configure_logging
from app.schemas.health import HealthResponse
from app.services.dispatcher import InProcessScanDispatcher
from app.services.home_service import HomeService
from app.services.publish_service import PublishService
from app.services.scan_service import ScanService
from app.services.social_service import SocialService
from app.services.usage_service import UsageService
from app.storage.retention import sweep_unreferenced

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    session_factory=None,
    scan_service=None,
    dispatcher=None,
    oauth=None,
    publish_service=None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    factory = session_factory or create_session_factory(settings.database_url)
    service = scan_service or ScanService(factory, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.dispatcher = dispatcher or InProcessScanDispatcher(
            service.run, settings.scan_workers, settings.scan_queue_size
        )
        if settings.app_environment == "development":
            from app.dev.demo import DemoScanService, FixtureCollector, FixtureScanner

            app.state.demo_service = DemoScanService(
                factory, settings, collector=FixtureCollector(), scanner=FixtureScanner()
            )
            app.state.demo_dispatcher = InProcessScanDispatcher(app.state.demo_service.run, 1, 2)

        def sweep_snapshots():
            with factory() as session:
                sweep_unreferenced(session, service.snapshot_store)

        async def purge_uploads():
            # Expired prepared uploads are removed even if nobody uploads again.
            while True:
                await asyncio.sleep(settings.upload_purge_interval_seconds)
                try:
                    await run_in_threadpool(app.state.publish_service.staging.purge_expired)
                    await run_in_threadpool(sweep_snapshots)
                except Exception as error:
                    logger.error("action=upload_purge_failed error_type=%s", type(error).__name__)

        purge_task = asyncio.create_task(purge_uploads())
        try:
            yield
        finally:
            purge_task.cancel()
            await run_in_threadpool(app.state.dispatcher.close)
            if hasattr(app.state, "demo_dispatcher"):
                await run_in_threadpool(app.state.demo_dispatcher.close)
            if session_factory is None:
                factory.kw["bind"].dispose()

    app = FastAPI(title="Terraform Module Intelligence", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.scan_service = service
    app.state.oauth = oauth or GitHubOAuth(settings)
    app.state.publish_service = publish_service or PublishService(factory, settings)
    app.state.social_service = SocialService(factory, settings)
    app.state.home_service = HomeService(factory, app.state.social_service)
    app.state.usage_service = UsageService(factory, settings, service.snapshot_store)
    app.add_middleware(
        CORSMiddleware,
        # Cookies are sent cross-origin, so origins are always an explicit list (never "*").
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["Location", "Content-Disposition"],
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError):
        body = {"code": getattr(error, "code", type(error).__name__), "message": str(error)}
        if getattr(error, "details", None):
            body["details"] = error.details
        return JSONResponse(status_code=error.status_code, content={"error": body})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        # FastAPI's default repeats what the caller sent ("input"); form values must not come
        # back in responses, so only where and why is reported.
        detail = []
        for item in error.errors():
            where = list(item.get("loc", ()))
            if item.get("type") == "extra_forbidden":
                where = where[:-1]  # The unexpected key is caller text too.
            detail.append({"type": item.get("type"), "loc": where, "msg": item.get("msg")})
        return JSONResponse(
            status_code=422, content={"detail": detail}, headers={"Cache-Control": "no-store"}
        )

    for router in (
        auth.router,
        uploads.router,
        social.router,
        usage.router,
        repositories.router,
        correlations.router,
        scans.router,
        findings.router,
        graphs.router,
        flows.router,
    ):
        app.include_router(router, prefix="/api")

    if settings.app_environment == "development":
        from app.dev.demo import router as demo_router
        from app.dev.oauth import router as fake_oauth_router

        app.include_router(demo_router)
        app.include_router(fake_oauth_router)

    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health(session: SessionDep):
        database = "ok"
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            session.rollback()
            database = "unavailable"
        checkov = "available" if shutil.which(settings.checkov_binary) else "unavailable"
        return HealthResponse(
            status="ok" if database == "ok" and checkov == "available" else "degraded",
            database=database,
            checkov=checkov,
        )

    return app


app = create_app()

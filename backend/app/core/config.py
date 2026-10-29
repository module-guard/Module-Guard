from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_environment: Literal["production", "development", "test"] = "production"

    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence"
    )
    workspace_root: Path = Path("/tmp/terraform-intelligence")
    keep_scan_workspace: bool = False
    snapshot_root: Path = Path("/tmp/terraform-intelligence-snapshots")
    # Prepared uploads wait here (at most upload_ttl_minutes) until registered.
    upload_root: Path = Path("/tmp/terraform-intelligence-uploads")
    upload_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    max_upload_zip_mb: int = Field(default=10, ge=1)
    max_upload_uncompressed_mb: int = Field(default=50, ge=1)
    max_upload_files: int = Field(default=1000, ge=1)
    upload_preflights_per_hour: int = Field(default=20, ge=1)
    max_running_publishes: int = Field(default=3, ge=1)
    forks_per_hour: int = Field(default=20, ge=1)
    # Logins nobody can hold by signing in (compared without case). System accounts are
    # reserved as well, whether or not they are listed here.
    reserved_logins: list[str] = [
        "terraform-aws-modules",
        "terraform-aws-modules-mirror",
        "moduleguard",
        "admin",
        "system",
    ]
    downloads_per_hour: int = Field(default=30, ge=1)
    usage_previews_per_hour: int = Field(default=600, ge=1)
    max_repositories_per_user: int = Field(default=100, ge=1)
    upload_purge_interval_seconds: int = Field(default=300, ge=1)
    max_snapshot_size_mb: int = Field(default=50, ge=1)
    checkov_binary: str = "checkov"
    checkov_timeout_seconds: int = Field(default=300, ge=1)
    secret_scan_timeout_seconds: int = Field(default=60, ge=1)
    git_timeout_seconds: int = Field(default=120, ge=1)
    # Terraform Registry metadata lookups. The host is fixed by configuration and never by
    # user input, so a submitted address cannot redirect the request elsewhere (SSRF).
    registry_base_url: str = "https://registry.terraform.io"
    registry_timeout_seconds: int = Field(default=10, ge=1)
    max_repository_size_mb: int = Field(default=200, ge=1)
    max_process_output_mb: int = Field(default=50, ge=1)
    scan_workers: int = Field(default=2, ge=1, le=16)
    scan_queue_size: int = Field(default=20, ge=0, le=1000)
    # Single uvicorn worker (see Dockerfile): on startup no scan can still be running, so
    # every non-terminal scan is orphaned and recovered. Set False only for a multi-process
    # deployment, where a scan whose runtime heartbeat is still fresh is left to its worker.
    scan_recovery_assume_single_worker: bool = True
    # Multi-process only: a running scan older than this (no heartbeat refresh) is treated as
    # abandoned. Kept above the longest single step (checkov_timeout + git_timeout + margin).
    scan_recovery_stale_seconds: int = Field(default=900, ge=1)
    max_analysis_entities: int = Field(default=20000, ge=1, le=100000)
    max_analysis_edges: int = Field(default=100000, ge=1, le=500000)
    max_local_module_depth: int = Field(default=32, ge=1, le=100)
    max_terraform_file_bytes: int = Field(default=2097152, ge=1)
    max_flow_edges: int = Field(default=100000, ge=1, le=500000)
    max_flow_paths: int = Field(default=20, ge=1, le=100)
    max_flow_depth: int = Field(default=30, ge=1, le=30)
    max_flow_search_steps: int = Field(default=100000, ge=1, le=1000000)
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # GitHub login. All three secrets are required; without them only login is unavailable.
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    session_secret: SecretStr | None = None
    # Public addresses as the browser sees them: redirect targets and the OAuth callback.
    frontend_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    # Overridable only for the development fake provider (app/dev/oauth.py).
    github_authorize_url: str = GITHUB_AUTHORIZE_URL
    github_token_url: str = GITHUB_TOKEN_URL
    github_user_url: str = GITHUB_USER_URL

    @field_validator("github_client_id", "github_client_secret", "session_secret", mode="before")
    @classmethod
    def blank_is_unset(cls, value):
        # Compose passes "${NAME:-}" as an empty string when the variable is not set.
        return value or None

    @field_validator("registry_base_url")
    @classmethod
    def registry_https_only(cls, value: str) -> str:
        # Plaintext or other schemes are refused so the lookup cannot be downgraded.
        if not value.startswith("https://"):
            raise ValueError("REGISTRY_BASE_URL must be an https:// URL.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def reject_unsafe_combinations(self):
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list explicit origins; credentials forbid '*'.")
        if self.app_environment == "production" and (
            self.github_authorize_url,
            self.github_token_url,
            self.github_user_url,
        ) != (GITHUB_AUTHORIZE_URL, GITHUB_TOKEN_URL, GITHUB_USER_URL):
            raise ValueError("GitHub OAuth endpoints cannot be overridden in production.")
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret and self.session_secret)

    @property
    def cookie_secure(self) -> bool:
        # Decided by configuration, never by request headers a client could forge.
        return self.api_public_url.startswith("https://")

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_days * 86400

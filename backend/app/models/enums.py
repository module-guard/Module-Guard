from enum import StrEnum


class SourceType(StrEnum):
    GITHUB = "GITHUB"
    TERRAFORM_REGISTRY = "TERRAFORM_REGISTRY"
    UPLOAD = "UPLOAD"


class ScanStatus(StrEnum):
    QUEUED = "QUEUED"
    COLLECTING = "COLLECTING"
    SCANNING = "SCANNING"
    NORMALIZING = "NORMALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"
    UNKNOWN = "UNKNOWN"


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class OriginType(StrEnum):
    GITHUB = "GITHUB"
    UPLOAD = "UPLOAD"
    FORK = "FORK"

class DomainError(Exception):
    status_code = 400


class UnsupportedSourceError(DomainError):
    status_code = 422


class RegistryNotSupportedError(DomainError):
    status_code = 501


class RegistryModuleNotFoundError(DomainError):
    status_code = 404


class RepositoryCloneError(DomainError):
    status_code = 502


class TerraformNotFoundError(DomainError):
    status_code = 422


class UnsafeRepositoryError(DomainError):
    status_code = 422


class RepositoryTooLargeError(DomainError):
    status_code = 413


class CheckovNotInstalledError(DomainError):
    status_code = 503


class CheckovExecutionError(DomainError):
    status_code = 502


class ScanTimeoutError(DomainError):
    status_code = 504


class InvalidCheckovResultError(DomainError):
    status_code = 502


class ProcessOutputLimitError(DomainError):
    status_code = 413


class NotFoundError(DomainError):
    status_code = 404


class ScanQueueFullError(DomainError):
    status_code = 503


class AuthenticationRequiredError(DomainError):
    status_code = 401


class ForbiddenOriginError(DomainError):
    status_code = 403


class LoginUnavailableError(DomainError):
    status_code = 503


class SnapshotError(DomainError):
    status_code = 500


class UploadRejectedError(DomainError):
    """The submitted source may not be stored. details lists each reason for the user."""

    status_code = 422

    def __init__(self, message: str, code: str, details: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


class UploadTooLargeError(UploadRejectedError):
    status_code = 413


class SystemAccountError(DomainError):
    status_code = 403


class RateLimitedError(DomainError):
    status_code = 429


class ConflictError(DomainError):
    status_code = 409


class PreparedSourceMissingError(DomainError):
    status_code = 410

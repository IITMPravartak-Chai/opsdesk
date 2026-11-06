"""Framework-neutral domain errors. The FastAPI layer maps ``http_status`` / ``code`` onto responses."""
from __future__ import annotations


class OpsDeskError(Exception):
    http_status = 400
    code = "error"
    default_detail = "Request could not be processed."

    def __init__(self, detail: str | dict | list | None = None, *, code: str | None = None):
        self.detail = detail if detail is not None else self.default_detail
        if code:
            self.code = code
        super().__init__(str(self.detail))


class ValidationFailed(OpsDeskError):
    http_status = 400
    code = "invalid"


class NotFound(OpsDeskError):
    http_status = 404
    code = "not_found"
    default_detail = "Not found."


class PermissionDenied(OpsDeskError):
    http_status = 403
    code = "permission_denied"
    default_detail = "You do not have permission to perform this action."


class Conflict(OpsDeskError):
    http_status = 409
    code = "conflict"


class SecurityError(PermissionDenied):
    """Raised when an insecure operation or prohibited bypass is attempted."""
    code = "security_error"
    default_detail = "Security policy violation."


class ReplayProtectionError(PermissionDenied):
    code = "replay_protection_failed"
    default_detail = "Tenant context replay protection failure."


class ReplayDetectedError(ReplayProtectionError):
    code = "replay_detected"
    default_detail = "Signed tenant context has already been claimed (replay detected)."


class InvalidTenantContextError(ReplayProtectionError):
    code = "invalid_tenant_context"
    default_detail = "Invalid, malformed, or expired signed tenant context."


class ReplayServiceUnavailableError(OpsDeskError):
    http_status = 503
    code = "replay_service_unavailable"
    default_detail = "Replay protection service temporarily unavailable. Fail closed."

"""Errors a call can end with: an HTTP code plus a human text that says what to do.

The same text goes to HTTP callers (`{"error": code, "message": text}`) and to
MCP (a tool error). Providers raise these; anything else becomes 502.
"""
from __future__ import annotations

PAGE = "motor.gang.su"


class MotorError(Exception):
    status = 500
    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def body(self) -> dict:
        return {"error": self.code, "message": self.message}


class BadRequest(MotorError):
    status = 400
    code = "bad_request"


class Unauthorized(MotorError):
    """The caller came without a valid motor token (not a provider token)."""

    status = 401
    code = "motor_token"


class TokenExpired(MotorError):
    """The provider's own token is missing or no longer accepted."""

    status = 401
    code = "provider_token"


class WriteForbidden(MotorError):
    status = 403
    code = "write_forbidden"


class NotFound(MotorError):
    status = 404
    code = "not_found"


class MethodNotAllowed(MotorError):
    status = 405
    code = "method_not_allowed"


class ProviderFailed(MotorError):
    """The provider answered something the motor did not expect."""

    status = 502
    code = "provider_error"


class Unavailable(MotorError):
    """The provider (or the way to it, e.g. the Avito VPN) is down."""

    status = 503
    code = "provider_down"

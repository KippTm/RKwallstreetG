"""Custom exceptions for Zettle API client."""

class ZettleError(Exception):
    """Base exception for all Zettle API errors."""
    pass


class ZettleAuthError(ZettleError):
    """Raised when authentication fails or API credentials are invalid/missing."""
    pass


class ZettleNotFoundError(ZettleError):
    """Raised when a requested resource (product, variant, etc.) is not found."""
    pass


class ZettleRequestError(ZettleError):
    """Raised when an API request fails."""
    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_API = 5
EXIT_REJECTED = 6


class FireflyError(Exception):
    """Base class for errors the CLI knows how to report."""

    exit_code = EXIT_ERROR


class NotLoggedIn(FireflyError):
    exit_code = EXIT_AUTH

    def __init__(self, detail: str = "") -> None:
        message = "Not signed in to Adobe Firefly. Run 'firefly login'."
        super().__init__(f"{message} ({detail})" if detail else message)


class SessionExpired(FireflyError):
    exit_code = EXIT_AUTH

    def __init__(self, detail: str = "") -> None:
        message = "The Adobe token has expired and could not be renewed. Run 'firefly login' again."
        super().__init__(f"{message} ({detail})" if detail else message)


class LoginFailed(FireflyError):
    exit_code = EXIT_AUTH


class NotFound(FireflyError):
    exit_code = EXIT_NOT_FOUND


class Rejected(FireflyError):
    """Adobe understood the request and refused it: content policy, or out of credits."""

    exit_code = EXIT_REJECTED


class ApiError(FireflyError):
    """Non-2xx from Adobe, carrying whatever the service explained."""

    exit_code = EXIT_API

    def __init__(self, status: int, message: str, body: object = None, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url

    def to_dict(self) -> dict[str, object]:
        return {
            "error": str(self),
            "exit_code": self.exit_code,
            "status": self.status,
            "url": self.url,
            "body": self.body,
        }


class Unreachable(FireflyError):
    """Adobe did not answer at all: DNS, TLS, timeout or a closed port."""


class JobFailed(FireflyError):
    """The generation job was accepted, then finished in a failed state."""

    exit_code = EXIT_API

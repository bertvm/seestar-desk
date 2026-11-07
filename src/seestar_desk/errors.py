"""Errors returned by a Seestar or by this desk."""


class ScopeError(Exception):
    def __init__(self, message: str, response: dict | None = None) -> None:
        super().__init__(message)
        self.response = response

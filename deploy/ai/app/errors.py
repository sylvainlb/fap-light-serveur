"""Erreur métier -> code HTTP + code d'erreur stable (annexe C)."""


class RunError(Exception):
    def __init__(self, http: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.http = http
        self.code = code
        self.message = message
        self.extra = extra or {}

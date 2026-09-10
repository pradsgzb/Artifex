"""Optional API-key authentication for the embedded server."""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status


class ApiKeyVerifier:
    def __init__(self, environment_variable: str):
        self.environment_variable = environment_variable

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.environment_variable, ""))

    async def __call__(self, x_api_key: str | None = Header(default=None)) -> None:
        expected = os.getenv(self.environment_variable, "")
        if not expected:
            return
        supplied = x_api_key or ""
        if not hmac.compare_digest(
            supplied.encode("utf-8", errors="surrogatepass"),
            expected.encode("utf-8", errors="surrogatepass"),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid X-API-Key header is required.",
                headers={"WWW-Authenticate": "ApiKey"},
            )

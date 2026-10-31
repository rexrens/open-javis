"""Browser trust fence and launch-token cookie for the web host.

Same posture as dsh: the server binds loopback only, the launch URL carries a
one-shot token that is exchanged for a signed host-only cookie, and every
``/api`` request must pass a Host/Origin check before authentication.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping

#: Cookie lifetime, matching dsh's default browser session window.
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

#: Hostnames that count as this machine for the trust fence.
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


class BrowserAuth:
    """Per-process launch token, signed cookie, and request trust checks."""

    def __init__(self, host: str, port: int, *, token: str | None = None) -> None:
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self._secret = secrets.token_bytes(32)
        self._cookie_name = f"javis_web_{port}"

    @property
    def cookie_name(self) -> str:
        """Host-only cookie carrying the browser session."""
        return self._cookie_name

    def authorities(self) -> set[str]:
        """Accepted ``Host`` values, each with the listening port."""
        names = {self.host} | (LOOPBACK_NAMES if self.host in LOOPBACK_NAMES else set())
        return {f"{name}:{self.port}" for name in names}

    def authenticated_url(self, base_url: str) -> str:
        """Root URL carrying this process's launch token."""
        return f"{base_url}/?token={self.token}"

    def _signature(self, expiry: int) -> str:
        payload = f"{self.host}|{self.port}|{expiry}".encode()
        digest = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def issue_cookie(self) -> str:
        """Build the ``Set-Cookie`` value for one authenticated browser session."""
        expiry = int(time.time()) + COOKIE_MAX_AGE_SECONDS
        return (
            f"{self._cookie_name}={expiry}.{self._signature(expiry)}; Path=/; "
            f"HttpOnly; SameSite=Strict; Max-Age={COOKIE_MAX_AGE_SECONDS}"
        )

    def cookie_is_valid(self, value: str) -> bool:
        """Verify one cookie value, including its absolute expiry."""
        expiry_text, _, signature = value.partition(".")
        if not expiry_text or not signature:
            return False
        try:
            expiry = int(expiry_text)
        except ValueError:
            return False
        if expiry < int(time.time()):
            return False
        return hmac.compare_digest(signature, self._signature(expiry))

    def trust_rejection(self, headers: Mapping[str, str]) -> int | None:
        """Return 403 when the request host or origin is not trusted."""
        host = headers.get("host", "")
        if host not in self.authorities():
            return 403
        origin = headers.get("origin")
        if origin and origin != f"http://{host}":
            return 403
        if headers.get("sec-fetch-site", "").lower() == "cross-site":
            return 403
        return None

    def is_authenticated(self, headers: Mapping[str, str]) -> bool:
        """Whether the request carries a valid browser-session cookie."""
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self._cookie_name:
                return self.cookie_is_valid(value)
        return False

    def request_rejection(self, headers: Mapping[str, str]) -> int | None:
        """Trust fence then authentication, in dsh's order."""
        return self.trust_rejection(headers) or (
            None if self.is_authenticated(headers) else 401
        )

    def index_exchange(self, headers: Mapping[str, str], query_token: str | None) -> str | None:
        """Decide one index request.

        Returns:
            ``"redirect"`` when the launch token was accepted and a cookie must be
            set, or None when an existing session may simply be served.

        Raises:
            PermissionError: carries ``"forbidden"`` or ``"unauthorized"``.
        """
        if self.trust_rejection(headers) is not None:
            raise PermissionError("forbidden")
        if query_token is not None and secrets.compare_digest(query_token, self.token):
            return "redirect"
        if self.is_authenticated(headers):
            return None
        raise PermissionError("unauthorized")

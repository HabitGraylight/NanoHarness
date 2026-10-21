"""Helpers for keeping credentials out of inspectable configuration."""

from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_QUERY_KEYS = {
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}


def redact_url_query(url: Optional[str]) -> Optional[str]:
    """Redact credential-like URL query values while preserving routing data."""
    if url is None:
        return None
    parsed = urlsplit(url)
    if not parsed.query:
        return url
    redacted = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.lower().replace("-", "_")
        sensitive = (
            normalized in _SENSITIVE_QUERY_KEYS
            or any(
                marker in normalized
                for marker in ("credential", "password", "secret", "signature", "token")
            )
        )
        redacted.append((key, "***" if sensitive else value))
    return urlunsplit(parsed._replace(query=urlencode(redacted, doseq=True, safe="*")))

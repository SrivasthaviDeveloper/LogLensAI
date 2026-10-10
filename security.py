"""Small redaction helpers for user-supplied and retrieved diagnostic text."""

import re


_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|client[_-]?secret|secret|access[_-]?token|refresh[_-]?token|api[_-]?key)\b\s*([:=])\s*([^\s,;]+)"
)
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")


def redact_sensitive_text(value: str) -> str:
    """Replace common secret forms before text enters reports or model queries."""

    value = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    return _AWS_KEY.sub("[REDACTED ACCESS KEY]", value)

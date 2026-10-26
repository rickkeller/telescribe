"""Deterministic redaction for data that is persisted by TeleScribe."""

import re
from typing import Any


_SECRET_VALUE = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|(?:auth(?:entication|orization)?[_-]?)?token|"
    r"secret|password|authorization|private[_-]?key|passphrase|account(?:[_-]?number)?|"
    r"routing(?:[_-]?number)?|bank[_-]?account|swift|bic)$",
    re.I,
)
_TELEGRAM_EPOCH_KEY = re.compile(r"^(?:date|edited)_unixtime$", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)(\b(?:api[ _-]?key|access[ _-]?token|auth(?:entication|orization)?[ _-]?token|"
    r"token|secret|password|passphrase)\s*(?::|=|\bis\b)\s*[\"']?)([^\s,;\"']+)([\"']?)"
)
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----[\s\S]*?-----END(?: [A-Z]+)* PRIVATE KEY-----")
_BEARER = re.compile(r"(?i)(\bauthorization\s*:\s*)?(\bbearer\s+)[\"']?([^\s,;\"']+)[\"']?")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(
    r"(?<!\w)(?:\+\d[\d .()/-]{5,}\d|(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}|\d{10})(?!\w)"
)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_FINANCIAL_VALUE = re.compile(
    r"(?i)(\b(?:account(?:[_ -]?number)?|routing(?:[_ -]?number)?|bank[_ -]?account|swift|bic)"
    r"\s*(?:(?::|=|\bis\b)\s*)?[\"']?)([A-Z0-9-]{6,})([\"']?)"
)


def redact_text(value: str) -> str:
    """Remove common credential values without changing unrelated user text."""
    value = _PRIVATE_KEY.sub(_SECRET_VALUE, value)
    value = _OPENAI_KEY.sub(_SECRET_VALUE, value)
    value = _TELEGRAM_TOKEN.sub(_SECRET_VALUE, value)
    value = _BEARER.sub(lambda match: f"{match.group(1) or ''}{match.group(2)}{_SECRET_VALUE}", value)
    value = _INLINE_SECRET.sub(r"\1" + _SECRET_VALUE + r"\3", value)
    value = _FINANCIAL_VALUE.sub(r"\1" + _SECRET_VALUE + r"\3", value)
    value = _EMAIL.sub(_SECRET_VALUE, value)
    value = _PHONE.sub(_SECRET_VALUE, value)
    value = _IBAN.sub(_SECRET_VALUE, value)
    return _CARD.sub(_SECRET_VALUE, value)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a recursively redacted JSON-compatible value.

    This deliberately runs before normalization so every persisted derivative
    (including ``original_json``) shares the same privacy boundary.
    """
    if key is not None and _SENSITIVE_KEY.search(key):
        return _SECRET_VALUE
    if key is not None and _TELEGRAM_EPOCH_KEY.fullmatch(key) and isinstance(value, str) and value.isdecimal():
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    return value

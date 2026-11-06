"""PII / secret redaction applied before any text leaves for an external LLM."""
import re

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}\b")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("SECRET", re.compile(r"(?i)\b(password|passwd|pwd|secret|token)\s*[:=]\s*\S+")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def redact(text: str) -> str:
    if not text:
        return text
    for label, rx in PATTERNS:
        if label == "SECRET":
            text = rx.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)
        else:
            text = rx.sub(f"[{label}]", text)
    return text

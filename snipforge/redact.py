"""Find sensitive text (emails, phone numbers, card/ID numbers) in OCR output."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QRectF

from .ocr import OcrLine

PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone": re.compile(r"(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


@dataclass(frozen=True)
class Finding:
    label: str
    text: str
    rect: QRectF


def find_sensitive(lines: tuple[OcrLine, ...] | list[OcrLine]) -> list[Finding]:
    findings: list[Finding] = []
    for line in lines:
        text, spans = _join(line)
        claimed: list[tuple[int, int]] = []
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                start, end = match.span()
                if any(start < c_end and end > c_start for c_start, c_end in claimed):
                    continue  # already covered by an earlier, more specific pattern
                rect = _union([w.rect for (s, e), w in zip(spans, line.words, strict=True) if s < end and e > start])
                if rect is not None:
                    claimed.append((start, end))
                    findings.append(Finding(label, match.group(), rect))
    return findings


def _join(line: OcrLine) -> tuple[str, list[tuple[int, int]]]:
    """Rebuild the line from its words, remembering each word's character span."""
    parts, spans, cursor = [], [], 0
    for word in line.words:
        spans.append((cursor, cursor + len(word.text)))
        parts.append(word.text)
        cursor += len(word.text) + 1
    return " ".join(parts), spans


def _union(rects: list[QRectF]) -> QRectF | None:
    if not rects:
        return None
    result = QRectF(rects[0])
    for r in rects[1:]:
        result = result.united(r)
    return result


def summarize(findings: list[Finding]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.label] = counts.get(f.label, 0) + 1
    names = {"email": "email", "card": "card number", "ssn": "SSN", "phone": "phone number", "ip": "IP address"}
    return ", ".join(f"{n} {names[k]}{'s' if n > 1 else ''}" for k, n in counts.items())

"""Pure helpers for long-document navigation; intentionally has no Qt dependency."""
from __future__ import annotations

import re


def page_matches_filter(page_number: int, query: str) -> bool:
    """Match a page filter: exact page, ranges/lists, or a forgiving text query.

    Examples: ``500``, ``page 500``, ``100-120``, ``1, 3, 7-9``.
    """
    q = query.strip().lower()
    if not q:
        return True
    q = re.sub(r"^(?:page|p)\s*#?\s*", "", q)
    if not q:
        return True
    if re.fullmatch(r"\d+", q):
        return page_number == int(q)
    if re.fullmatch(r"\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*", q):
        for part in q.split(","):
            bits = [int(v.strip()) for v in part.split("-")]
            if len(bits) == 1 and page_number == bits[0]:
                return True
            if len(bits) == 2 and min(bits) <= page_number <= max(bits):
                return True
        return False
    return q in str(page_number)


def merge_old_solving_data(existing_notes: str, data: dict) -> str | None:
    """The old Solve tab was merged into Notes. Returns the merged notes text, or None if there is nothing to carry over."""
    parts = [(label, str(data.get(key, "")).strip())
             for label, key in (("Question", "prompt"), ("Working", "working"), ("Answer", "answer"))]
    parts = [(label, text) for label, text in parts if text]
    if not parts:
        return None
    existing = existing_notes.rstrip()
    block = "\n\n".join(f"{label}\n{text}" for label, text in parts)
    return (existing + "\n\n" if existing else "") + "— From the old Solve tab —\n" + block

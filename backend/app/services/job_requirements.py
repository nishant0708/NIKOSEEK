"""Deterministic checks on a job description that must not depend on the LLM's judgement.

Experience gate: if the JD clearly asks for more years than you searched for (e.g. you want 0-1 years and the JD says
"3-5 years of professional experience"), the job is rejected even when the model liked it.
"""
from __future__ import annotations

import re

# "3-5 years", "3–5 yrs", "5+ years", "minimum 2 years", "at least 4 years" ...
_YEARS = re.compile(
    r"(?:(?:minimum|min\.?|at least|over)\s+(?:of\s+)?)?"
    r"(?P<low>\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(?P<high>\d{1,2})(?:\.\d)?\s*\+?\s*)?(?:years?|yrs?)\b",
    re.I,
)
# The years must be about experience, not "5 years of company history" or "2 year warranty".
_EXPERIENCE_CONTEXT = re.compile(r"experience|exp\b|professional|building|working|developing|hands[- ]on|industry|relevant|in (?:a |the )?(?:role|field)", re.I)
TOLERANCE_YEARS = 1  # a 2-year ask is still worth a try for a 0-1 year search


def jd_min_years(text: str | None) -> int | None:
    """The smallest experience requirement the JD states (most lenient reading), or None if it states none."""
    if not text:
        return None
    found: list[int] = []
    for match in _YEARS.finditer(text):
        after = text[match.end():match.end() + 60]
        before = text[max(0, match.start() - 40):match.start()]
        if not (_EXPERIENCE_CONTEXT.search(after) or re.search(r"experience\s*(?:of|:)?\s*$", before, re.I)):
            continue
        low = int(match.group("low"))
        if low <= 25:
            found.append(low)
    return min(found) if found else None


def preference_max_years(preference: str | None) -> int | None:
    """Upper end of the experience you searched for: "0-1 years" -> 1, "fresher" -> 0, "2" -> 2, "5+ years" -> None (no cap)."""
    value = (preference or "").strip().lower()
    if not value:
        return None
    if "+" in value or "above" in value or "more than" in value:
        return None
    numbers = [int(n) for n in re.findall(r"\d{1,2}", value)]
    if numbers:
        return max(numbers)
    if re.search(r"fresher|entry|graduate|intern|no experience", value):
        return 0
    return None


def experience_gate(description: str | None, preference: str | None) -> str | None:
    """A reason to reject the job when its minimum experience is clearly above your search, else None."""
    need, cap = jd_min_years(description), preference_max_years(preference)
    if need is None or cap is None or need <= cap + TOLERANCE_YEARS:
        return None
    return f"JD asks for {need}+ years of experience; you searched for {preference}."

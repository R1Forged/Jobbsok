from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import JobListing


INCLUDE_PATTERNS = [
    r"\bsupply chain\b",
    r"\blogistics?\b",
    r"\bplanning\b",
    r"\bS&OP\b",
    r"\bS\s*&\s*OE\b",
    r"\boperations?\b",
    r"\boperational excellence\b",
    r"\btransformation\b",
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bmanager\b",
    r"\bleder\b",
    r"\bsjef\b",
    r"\bplansjef\b",
    r"\bproduksjonsplanlegging\b",
    r"\blogistikk\b",
    r"\bvareflyt\b",
    r"\bdemand planning\b",
    r"\bSAP\b",
    r"\bRelex\b",
]

EXCLUDE_PATTERNS = [
    r"\blagermedarbeider\b",
    r"\bwarehouse worker\b",
    r"\btruckfører\b",
    r"\bforklift\b",
    r"\bsjåfør\b",
    r"\bdriver\b",
    r"\bterminalarbeider\b",
    r"\bterminal worker\b",
    r"\bkundeservice\b",
    r"\bcustomer service\b",
    r"\btrainee\b",
    r"\bjunior\b",
    r"\bintern(ship)?\b",
    r"\bbutikkmedarbeider\b",
    r"\bservitør\b",
    r"\bbartender\b",
    r"\bverver\b",
]


@dataclass(frozen=True)
class FilterResult:
    include: bool
    reason: str


def hard_filter(listing: JobListing) -> FilterResult:
    text = listing.combined_text
    exclude = _first_match(EXCLUDE_PATTERNS, text)
    if exclude:
        return FilterResult(False, f"Excluded by low-fit keyword: {exclude}")

    include = _first_match(INCLUDE_PATTERNS, text)
    if include:
        return FilterResult(True, f"Included by keyword: {include}")

    return FilterResult(False, "No required profile-match keyword found")


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return ""

"""Capability-boundary canned responses.

When the LLM detects a request it can't fulfill, it emits a tag
`[REFUSE:<category>(:key=value(,key=value)*)?]` at the START of its reply.
This module:

  1. Defines the canonical category set + canned response template per
     category. The template can interpolate `{key}` parameters parsed from
     the tag (e.g. `[REFUSE:future_data:year=2025]` → `{year}` available).
  2. Provides `dispatch(text)` which detects the tag and substitutes the
     canned text. Anything after the tag is ignored. No tag → text is
     returned unchanged.

Why tag-based instead of letting the LLM write the canned response itself:
  - Stable wording (LLMs paraphrase every turn — bad for ops/brand audit)
  - Trivially testable with snapshot tests
  - Adding a category is a 1-line edit here + 1-line in system_prompt.py
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The dispatch tag must be the first non-whitespace content in the reply.
# Inner pattern handles `[REFUSE:cat]` and `[REFUSE:cat:k1=v1,k2=v2]`.
_TAG_RE = re.compile(
    r"\A\s*\[REFUSE:([a-z_]+)(?::([^\]]*))?\]",
    re.IGNORECASE,
)


CANNED: dict[str, str] = {
    "off_topic": (
        "I can only answer questions about the US population using the "
        "**Snowflake US Open Census dataset** (SafeGraph ACS 5-year, 2019 "
        "and 2020 snapshots).\n\n"
        "I can help with **demographics, race, ethnicity, age, income, "
        "poverty, housing, employment, education, commute, and language** — "
        "broken down by state, county, or census block group.\n\n"
        "Try one of:\n"
        "- *What's the total population of California in 2020?*\n"
        "- *Median age by state*\n"
        "- *Vacancy rate in San Francisco*"
    ),
    "out_of_capability": (
        "I can't generate, synthesize, train on, or export census data — "
        "I'm a **query** agent over the existing Snowflake dataset, not a "
        "modeling or data-engineering tool.\n\n"
        "What I **can** do is pull real numbers from the dataset. For example:\n"
        "- *Total population by state*\n"
        "- *Households below poverty by county*\n"
        "- *Hispanic population in Los Angeles County*"
    ),
    "future_data": (
        "The dataset only covers **2019 and 2020 ACS 5-year snapshots** "
        "(covering 2015–2019 and 2016–2020). I don't have data for **{year}**.\n\n"
        "The most recent snapshot in this dataset is 2020. Want me to pull "
        "that instead?"
    ),
    "individual_data": (
        "I can't look up individual people, addresses, or sub-CBG geographies. "
        "The Census Bureau only releases **aggregated counts at the census-"
        "block-group level** (~600–3000 people each) to protect privacy.\n\n"
        "Can I help with a population-level question instead — e.g., "
        "*\"how many people live in this ZIP code?\"* or *\"median income by county\"*?"
    ),
    "personal_advice": (
        "I can answer factual questions about census data but can't give "
        "legal, medical, financial, or policy advice — and I can't take "
        "actions on your behalf.\n\n"
        "Is there a Census statistic I can pull for you?"
    ),
    "prompt_injection": (
        "I'll stick to answering questions about the US Census dataset.\n\n"
        "What demographic data can I help you find?"
    ),
    "non_additive_metric": (
        "Census ACS provides **{metric}** as a per-census-block-group value. "
        "Summing or averaging it across geographies is **statistically "
        "incorrect** — each block group is a separate sample with its own "
        "margin of error.\n\n"
        "I can show you the underlying counts (population, households, etc.) "
        "for the area you're interested in, and you can compute a "
        "population-weighted estimate from those. Want me to pull those?"
    ),
}


@dataclass(frozen=True)
class CannedVerdict:
    matched: bool
    category: str | None
    text: str  # always the user-facing reply (canned if matched, original if not)


def _parse_params(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k:
                out[k] = v
    return out


def dispatch(reply_text: str) -> CannedVerdict:
    """Inspect the LLM reply for a `[REFUSE:...]` tag and substitute canned text.

    If no tag is found, returns the input unchanged. If a tag is found but
    the category is unknown, falls back to `off_topic` (safest default) and
    logs a warning so we notice prompt drift.
    """
    if not reply_text:
        return CannedVerdict(matched=False, category=None, text=reply_text or "")

    m = _TAG_RE.match(reply_text)
    if not m:
        return CannedVerdict(matched=False, category=None, text=reply_text)

    category = m.group(1).lower()
    params = _parse_params(m.group(2))
    template = CANNED.get(category)
    if template is None:
        logger.warning("unknown REFUSE category %r — falling back to off_topic", category)
        category = "off_topic"
        template = CANNED[category]

    try:
        text = template.format(**params) if params else template
    except KeyError as exc:
        logger.warning(
            "canned template %r missing param %s — emitting raw template", category, exc
        )
        text = template
    return CannedVerdict(matched=True, category=category, text=text)

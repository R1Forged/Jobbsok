from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from openai import APIStatusError, AuthenticationError, OpenAI, RateLimitError

from .parser import JobListing


LOGGER = logging.getLogger(__name__)


SCORING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number"},
        "recommendation": {"type": "string", "enum": ["SØK", "VURDER", "DROPP"]},
        "why_relevant": {"type": "string"},
        "red_flags": {"type": "string"},
        "level_assessment": {"type": "string"},
        "salary_potential": {"type": "string"},
        "application_angle": {"type": "string"},
    },
    "required": [
        "score",
        "recommendation",
        "why_relevant",
        "red_flags",
        "level_assessment",
        "salary_potential",
        "application_angle",
    ],
}


@dataclass(frozen=True)
class ScoreResult:
    score: int
    recommendation: str
    why_relevant: str
    red_flags: str
    level_assessment: str
    salary_potential: str
    application_angle: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScoreResult":
        score = int(round(float(payload.get("score", 0))))
        score = max(0, min(100, score))
        recommendation = str(payload.get("recommendation", "DROPP")).strip().upper()
        if recommendation not in {"SØK", "VURDER", "DROPP"}:
            recommendation = "DROPP"
        return cls(
            score=score,
            recommendation=recommendation,
            why_relevant=str(payload.get("why_relevant", "")).strip(),
            red_flags=str(payload.get("red_flags", "")).strip(),
            level_assessment=str(payload.get("level_assessment", "")).strip(),
            salary_potential=str(payload.get("salary_potential", "")).strip(),
            application_angle=str(payload.get("application_angle", "")).strip(),
        )


class ScoringUnavailable(RuntimeError):
    pass


class JobScorer:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def score(self, listing: JobListing) -> ScoreResult:
        try:
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You are a careful Norwegian executive job-search screener. "
                            "Return only strict JSON matching the provided schema. "
                            "Score conservatively for Simen Eriksen Fricker's profile."
                        ),
                    },
                    {"role": "user", "content": _build_prompt(listing)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "job_score",
                        "strict": True,
                        "schema": SCORING_SCHEMA,
                    }
                },
            )
        except AuthenticationError as exc:
            raise ScoringUnavailable("OpenAI authentication failed. Check OPENAI_API_KEY.") from exc
        except RateLimitError as exc:
            raise ScoringUnavailable(
                "OpenAI scoring is unavailable due to rate limit or insufficient quota. "
                "Check OpenAI billing/usage or use another API key."
            ) from exc
        except APIStatusError as exc:
            raise ScoringUnavailable(f"OpenAI scoring failed with HTTP {exc.status_code}.") from exc
        raw_text = getattr(response, "output_text", "") or ""
        if not raw_text:
            LOGGER.debug("Raw OpenAI response without output_text: %r", response)
            raise RuntimeError("OpenAI response did not include output_text")
        return ScoreResult.from_dict(json.loads(raw_text))


def _build_prompt(listing: JobListing) -> str:
    description = listing.full_description[:12000]
    return f"""
USER PROFILE
Name: Simen Eriksen Fricker
Current role: Logistics Manager at Anora Group (Arcus Norway), Gjelleråsen plant.
Experience: logistics leadership, supply chain management, S&OP/S&OE, forecasting and planning,
production planning, export coordination, master data, SAP ECC, Relex, Excel/Power BI,
operational excellence, and AI/automation initiatives.

TARGET ROLES
Supply Chain Manager, Head of Supply Chain, Director Supply Chain, Director Planning,
Head of Planning, Senior Logistics Manager, Operational Excellence Manager,
Planning & Operational Excellence, transformation roles in operations/supply chain,
and AI/automation roles tied to operations.

GEOGRAPHY
Oslo, Akershus/Viken, hybrid, or remote Norway.

INDUSTRIES
FMCG, manufacturing, food & beverage, logistics/retail, industrial companies,
and senior-enough consulting.

EXCLUDE / PENALIZE
Warehouse worker, driver, terminal worker, junior, trainee, pure procurement unless strategic,
low-scope operational roles, and roles likely below a 960,000 NOK current base unless career upside is strong.

SCORING WEIGHTS
- 30% profile match in logistics/supply chain/planning
- 20% leadership/seniority
- 15% operational excellence/transformation
- 15% salary and career upside, prioritize 1.1MNOK+ potential or strong upside
- 10% industry relevance
- 10% geography fit

JOB
Title: {listing.title}
Company: {listing.company}
Location: {listing.location}
Deadline: {listing.deadline}
URL: {listing.url}
Snippet: {listing.snippet}
Description:
{description}
""".strip()

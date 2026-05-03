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
        "career_move_type": {
            "type": "string",
            "enum": ["STEP_UP", "LATERAL_WITH_UPSIDE", "LATERAL_LOW_UPSIDE", "STEP_DOWN"],
        },
        "headhunter_verdict": {"type": "string"},
        "why_relevant": {"type": "string"},
        "red_flags": {"type": "string"},
        "mandate_assessment": {"type": "string"},
        "level_assessment": {"type": "string"},
        "salary_potential": {"type": "string"},
        "application_angle": {"type": "string"},
        "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
    },
    "required": [
        "score",
        "recommendation",
        "career_move_type",
        "headhunter_verdict",
        "why_relevant",
        "red_flags",
        "mandate_assessment",
        "level_assessment",
        "salary_potential",
        "application_angle",
        "confidence",
    ],
}


@dataclass(frozen=True)
class ScoreResult:
    score: int
    recommendation: str
    career_move_type: str
    headhunter_verdict: str
    why_relevant: str
    red_flags: str
    mandate_assessment: str
    level_assessment: str
    salary_potential: str
    application_angle: str
    confidence: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScoreResult":
        score = int(round(float(payload.get("score", 0))))
        score = max(0, min(100, score))
        recommendation = str(payload.get("recommendation", "DROPP")).strip().upper()
        if recommendation not in {"SØK", "VURDER", "DROPP"}:
            recommendation = "DROPP"
        career_move_type = str(payload.get("career_move_type", "STEP_DOWN")).strip().upper()
        if career_move_type not in {"STEP_UP", "LATERAL_WITH_UPSIDE", "LATERAL_LOW_UPSIDE", "STEP_DOWN"}:
            career_move_type = "STEP_DOWN"
        confidence = str(payload.get("confidence", "LOW")).strip().upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            confidence = "LOW"
        return cls(
            score=score,
            recommendation=recommendation,
            career_move_type=career_move_type,
            headhunter_verdict=str(payload.get("headhunter_verdict", "")).strip(),
            why_relevant=str(payload.get("why_relevant", "")).strip(),
            red_flags=str(payload.get("red_flags", "")).strip(),
            mandate_assessment=str(payload.get("mandate_assessment", "")).strip(),
            level_assessment=str(payload.get("level_assessment", "")).strip(),
            salary_potential=str(payload.get("salary_potential", "")).strip(),
            application_angle=str(payload.get("application_angle", "")).strip(),
            confidence=confidence,
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
                            "You are a senior Norwegian executive recruiter and headhunter. "
                            "Return only strict JSON matching the provided schema. "
                            "Score conservatively for whether the job is a real career step for Simen Eriksen Fricker."
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
Current role: Logistics Manager at Anora Group / Arcus Norway, Gjelleråsen.
Current compensation: approximately 960k NOK base plus bonus/benefits.
Responsibilities and strengths: leadership across logistics, export, planning, master data, and supply chain;
S&OP/S&OE, forecast, production planning, SAP ECC, Relex, Power BI/Excel; operational excellence,
AI automation, process improvement, and supply chain transformation.

CAREER GOAL
Find roles with higher mandate, broader strategic scope, transformation responsibility,
planning excellence, supply chain leadership, or AI/automation in operations.
He is not interested in lateral operational roles without clear mandate, compensation upside, or brand/career value.

GEOGRAPHY
Oslo, Akershus/Viken, hybrid, or remote Norway.

INDUSTRIES
FMCG, manufacturing, food & beverage, logistics/retail, industrial companies,
and senior-enough consulting.

SCORING WEIGHTS
- 20% Role step-up: Is this truly a step up from Logistics Manager?
- 20% Mandate/influence: Team, budget, strategy, leadership team exposure, transformation remit
- 15% Profile match: Supply chain, logistics, planning, S&OP, SAP/Relex, export, manufacturing/FMCG
- 15% Transformation/OE/AI potential: Improvement, digitalization, automation, transformation
- 15% Compensation/career upside: Likely >1.1MNOK total comp or meaningful career value
- 10% Industry relevance
- 5% Geography/hybrid fit

HARD RECRUITER RULES
- Penalize lateral moves unless title, scope, brand, or compensation upside is clearly strong.
- Penalize pure operational firefighting.
- Penalize coordinator/specialist roles unless unusually strategic.
- Penalize roles likely below current compensation.
- Reward Director, Head, Senior Manager, broad supply-chain leadership, planning excellence, transformation,
  automation, SAP/S4, IBP, S&OP, and supply chain strategy.
- Use LOW confidence when the listing lacks enough detail about mandate, compensation, or scope.

JOB
Source: {listing.source}
Title: {listing.title}
Company: {listing.company}
Location: {listing.location}
Deadline: {listing.deadline}
URL: {listing.url}
Snippet: {listing.snippet}
Description:
{description}
""".strip()

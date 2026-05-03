from __future__ import annotations

import logging

import requests

from .parser import JobListing
from .scoring import ScoreResult


LOGGER = logging.getLogger(__name__)


def build_message(listing: JobListing, score: ScoreResult) -> str:
    return f"""🚀 Jobbmatch: {score.score}/100 – {listing.title}
Selskap: {listing.company or "Ukjent"}
Sted: {listing.location or "Ikke oppgitt"}
Kilde: {listing.source}
Anbefaling: {score.recommendation}
Karrieretype: {score.career_move_type}

Headhunter-vurdering:
{score.headhunter_verdict or "Ikke oppgitt"}

Hvorfor relevant:
{score.why_relevant or "Ikke oppgitt"}

Mandat:
{score.mandate_assessment or "Ikke oppgitt"}

Røde flagg:
{score.red_flags or "Ingen tydelige røde flagg oppgitt"}

Nivå:
{score.level_assessment or "Ikke oppgitt"}

Lønnspotensial:
{score.salary_potential or "Ikke oppgitt"}

Søknadsvinkel:
{score.application_angle or "Ikke oppgitt"}

Link:
{listing.url}"""


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, dry_run: bool = False) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run

    def send(self, message: str) -> bool:
        if self.dry_run:
            LOGGER.info("DRY_RUN=true. Telegram message not sent:\n%s", message)
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message[:4096],
                    "disable_web_page_preview": False,
                },
                timeout=20,
            )
        except requests.RequestException:
            LOGGER.exception("Telegram send failed")
            return False

        if response.status_code >= 400:
            LOGGER.error("Telegram returned HTTP %s: %s", response.status_code, response.text[:500])
            return False
        return True

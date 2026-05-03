# Personal FINN Job Search Agent

Private Python job-search monitor for Simen Eriksen Fricker. It checks saved FINN job searches twice per day with conservative request limits, scores new listings with OpenAI, and sends only high-quality matches to Telegram.

This is intended as a personal monitor. Do not use it to bypass login, CAPTCHA, anti-bot systems, or FINN access controls. Keep the search set narrow, the page count low, and the request delay respectful.

## What It Does

- Fetches the first 1-3 pages from saved FINN job-search URLs.
- Stores seen jobs in SQLite to avoid duplicate processing and alerts.
- Fetches detail pages only for new listings, capped per run.
- Applies a hard keyword filter before AI scoring.
- Scores jobs against Simen's supply chain, logistics, planning, operational excellence, and AI/automation profile.
- Sends Telegram alerts only when `score >= MIN_SCORE`.
- Runs locally or on GitHub Actions twice daily.

## Project Structure

```text
.
├── README.md
├── requirements.txt
├── .env.example
├── .github/workflows/job-agent.yml
├── data/.gitkeep
└── src
    ├── main.py
    ├── fetch_finn.py
    ├── parser.py
    ├── filters.py
    ├── scoring.py
    ├── telegram.py
    ├── db.py
    └── config.py
```

## Local Setup

Use Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your real values, then run:

```bash
python -m src.main
```

For a no-alert test:

```bash
DRY_RUN=true python -m src.main
```

On PowerShell:

```powershell
$env:DRY_RUN="true"
python -m src.main
```

## Environment Variables

Required:

- `OPENAI_API_KEY`: OpenAI API key used for job scoring.
- `TELEGRAM_BOT_TOKEN`: Telegram bot token from BotFather.
- `TELEGRAM_CHAT_ID`: Chat ID to receive alerts.
- `FINN_SEARCH_URLS`: Comma-separated FINN search URLs.

Optional:

- `MIN_SCORE`: Alert threshold. Default: `75`.
- `REQUEST_DELAY_SECONDS`: Delay between FINN requests. Default: `3`.
- `MAX_DETAIL_FETCHES_PER_RUN`: Max detail pages fetched per run. Default: `20`.
- `MAX_NEW_JOBS_PER_RUN`: Max new jobs considered per run. Default: `20`.
- `DRY_RUN`: `true` logs Telegram messages instead of sending. Default: `false`.
- `DB_PATH`: SQLite path. Default: `data/jobs.sqlite`.
- `OPENAI_MODEL`: Scoring model. Default: `gpt-4.1-mini`.
- `FINN_MAX_PAGES_PER_SEARCH`: First pages to fetch per search, max `3`. Default: `3`.
- `LOG_LEVEL`: Default: `INFO`.

## FINN Search URLs

Create narrow searches on FINN and copy the result URLs. Good starting points:

- Supply chain / planning roles around Oslo and Akershus.
- Logistics manager and operational excellence roles.
- Director, head of, and senior manager searches.
- Remote Norway or hybrid filters if relevant.

Put them in `FINN_SEARCH_URLS` as a comma-separated list:

```env
FINN_SEARCH_URLS=https://www.finn.no/job/search?location=1.20001.20061&occupation=1.31.226,https://www.finn.no/job/search?location=1.20001.20061&q=supply%20chain,https://www.finn.no/job/search?location=1.20001.20061&q=operational%20excellence,https://www.finn.no/job/search?location=1.20001.20061&q=planning,https://www.finn.no/job/search?location=1.20001.20061&q=logistikk,https://www.finn.no/job/search?q=supply%20chain%20manager,https://www.finn.no/job/search?q=head%20of%20supply%20chain,https://www.finn.no/job/search?q=director%20supply%20chain,https://www.finn.no/job/search?q=operational%20excellence,https://www.finn.no/job/search?q=S%26OP
```

This search strategy combines targeted Oslo/Akershus/Viken searches with broader senior supply-chain searches. Encoded query characters such as `%20` and `%26` are safe because the app splits only on commas.

The agent adds only a `page` query parameter for pages 2-3 and does not attempt login, CAPTCHA solving, or other bypass behavior.

## Telegram Setup

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`, choose a name and username, and copy the bot token.
3. Start a chat with your new bot and send any message.
4. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
5. Run the helper:

```bash
python scripts/telegram_setup.py --write-env
```

It validates the token, lists available numeric chat ids, and writes `TELEGRAM_CHAT_ID` automatically when exactly one chat is found.

Manual fallback: visit this URL in a browser, replacing the token:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates
```

Find `chat.id` in the JSON response. Use that numeric value as `TELEGRAM_CHAT_ID`.

`TELEGRAM_CHAT_ID` is not the bot username, not a `t.me` URL, and not an `@handle`.

If you want alerts in a group, add the bot to the group, send a message in the group, and call `getUpdates` again. Group chat IDs are often negative numbers.

## GitHub Actions Setup

Create a private GitHub repo and push this project. Then add repository secrets:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `FINN_SEARCH_URLS`

Optional repository variables:

- `MIN_SCORE`
- `REQUEST_DELAY_SECONDS`
- `MAX_DETAIL_FETCHES_PER_RUN`
- `MAX_NEW_JOBS_PER_RUN`
- `DRY_RUN`
- `OPENAI_MODEL`
- `FINN_MAX_PAGES_PER_SEARCH`

The workflow runs at `06:15` and `18:15` UTC and can also be started manually from the Actions tab. GitHub schedules are in UTC, so adjust `.github/workflows/job-agent.yml` if you want different local times.

The SQLite database is cached between workflow runs using `actions/cache`. This prevents repeated alerts for already-seen listings without committing the database to git.

When `DRY_RUN=true`, the agent uses a separate `*.dry-run.sqlite` database so test runs do not consume jobs from the real alert database.

## Scoring Behavior

The AI returns strict JSON:

```json
{
  "score": 0,
  "recommendation": "SØK",
  "why_relevant": "",
  "red_flags": "",
  "level_assessment": "",
  "salary_potential": "",
  "application_angle": ""
}
```

Scoring weights:

- 30% profile match in logistics, supply chain, and planning.
- 20% leadership and seniority.
- 15% operational excellence or transformation.
- 15% salary and career upside.
- 10% industry relevance.
- 10% geography fit.

Adjust alert selectivity by changing `MIN_SCORE`. A useful operating range is `75-85`.

## Reliability Notes

- HTTP requests use retries for transient errors.
- FINN detail pages are fetched only for new listings.
- The run continues on partial failures.
- Telegram sends are skipped in `DRY_RUN=true`.
- All major steps log progress for GitHub Actions diagnostics.

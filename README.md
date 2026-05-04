# Personal Job Search Agent

Private Python job-search monitor for Simen Eriksen Fricker. It checks saved FINN job searches and optional LinkedIn Job Alert emails, scores new listings with OpenAI, and sends only high-quality matches to Telegram.

LinkedIn is not scraped directly. LinkedIn jobs are ingested only from Job Alert emails in your mailbox.

## What It Does

- Fetches the first 1-3 pages from saved FINN job-search URLs.
- Optionally reads LinkedIn Job Alert emails over IMAP without marking them as read.
- Stores seen jobs in SQLite to avoid duplicate processing and alerts.
- Fetches FINN detail pages only for new FINN listings.
- Applies a hard keyword filter before AI scoring.
- Scores jobs as a headhunter would: whether the role is a real career step for Simen.
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
├── scripts/telegram_setup.py
└── src
    ├── main.py
    ├── fetch_finn.py
    ├── fetch_email.py
    ├── parse_linkedin_email.py
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
python -m src.main
```

For a no-alert test:

```bash
DRY_RUN=true python -m src.main
```

## Environment Variables

Required:

- `OPENAI_API_KEY`: OpenAI API key used for job scoring.
- `TELEGRAM_BOT_TOKEN`: Telegram bot token from BotFather.
- `TELEGRAM_CHAT_ID`: Numeric chat ID to receive alerts.
- `FINN_SEARCH_URLS`: Comma-separated FINN search URLs.

Optional:

- `MIN_SCORE`: Alert threshold. Default: `75`.
- `REQUEST_DELAY_SECONDS`: Delay between FINN requests. Default: `3`.
- `MAX_DETAIL_FETCHES_PER_RUN`: Max new jobs processed per run. Default: `20`.
- `MAX_NEW_JOBS_PER_RUN`: Max new jobs collected per run. Default: `20`.
- `INITIAL_BACKFILL`: `true` to scan existing listings across deeper FINN result pages. Default: `false`.
- `BACKFILL_MAX_PAGES`: Pages per FINN search in backfill mode. Default: `5`.
- `BACKFILL_MAX_DETAIL_FETCHES`: Max listings processed in backfill mode. Default: `100`.
- `DRY_RUN`: `true` logs Telegram messages instead of sending. Default: `false`.
- `DB_PATH`: SQLite path. Default: `data/jobs.sqlite`.
- `OPENAI_MODEL`: Scoring model. Default: `gpt-4.1-mini`.
- `FINN_MAX_PAGES_PER_SEARCH`: First pages to fetch per search, max `3`. Default: `3`.
- `LOG_LEVEL`: Default: `INFO`.

Optional LinkedIn email ingestion:

- `ENABLE_EMAIL_INGESTION`: `true` to read LinkedIn Job Alert emails. Default: `false`.
- `EMAIL_HOST`: IMAP host, for example `imap.gmail.com`.
- `EMAIL_PORT`: IMAP SSL port. Default: `993`.
- `EMAIL_USERNAME`: Mailbox username. Store as a GitHub secret.
- `EMAIL_PASSWORD`: Mailbox password or app password. Store as a GitHub secret.
- `EMAIL_FOLDER`: Mailbox folder. Default: `INBOX`.
- `EMAIL_FROM_FILTER`: Sender filter. Default: `jobs-noreply@linkedin.com`.
- `EMAIL_SUBJECT_FILTER`: Subject filter. Default: `job`.
- `EMAIL_LOOKBACK_DAYS`: Look back this many days. Default: `7`.
- `MAX_EMAILS_PER_RUN`: Max emails scanned per run. Default: `20`.
- `EMAIL_POST_PROCESS_ACTION`: What to do after a LinkedIn alert email is parsed. Options: `none`, `archive`, `trash`. Default: `none`.

## FINN Search URLs

Put saved FINN searches in `FINN_SEARCH_URLS` as a comma-separated list:

```env
FINN_SEARCH_URLS=https://www.finn.no/job/search?location=1.20001.20061&occupation=1.31.226,https://www.finn.no/job/search?location=1.20001.20061&q=supply%20chain%20manager,https://www.finn.no/job/search?location=1.20001.20061&q=head%20of%20supply%20chain,https://www.finn.no/job/search?location=1.20001.20061&q=logistikksjef,https://www.finn.no/job/search?location=1.20001.20061&q=planning%20manager,https://www.finn.no/job/search?location=1.20001.20061&q=demand%20planning,https://www.finn.no/job/search?q=S%26OP,https://www.finn.no/job/search?q=operational%20excellence,https://www.finn.no/job/search?q=transformation%20manager,https://www.finn.no/job/search?q=SAP%20Relex%20IBP,https://www.finn.no/job/search?q=automation%20process%20improvement%20operations,https://www.finn.no/job/search?q=produksjonsplanlegging
```

This search strategy combines targeted Oslo/Akershus/Viken category and keyword searches with broader senior searches for supply chain, planning, S&OP, operational excellence, transformation, SAP/Relex/IBP, automation, and production planning. Encoded query characters such as `%20` and `%26` are safe because the app splits only on commas.

The agent adds only a `page` query parameter for pages 2-3 and does not attempt login, CAPTCHA solving, or other bypass behavior.

## Initial Backfill

Use backfill once when you want the bot to scan existing FINN listings, not only new listings.

Local dry-run example:

```bash
INITIAL_BACKFILL=true BACKFILL_MAX_PAGES=5 BACKFILL_MAX_DETAIL_FETCHES=100 DRY_RUN=true python -m src.main
```

For a real backfill, set `DRY_RUN=false`. After the first backfill run, set `INITIAL_BACKFILL=false` again so scheduled runs return to lightweight monitoring.

Backfill still stays conservative:

- It uses the same saved FINN URLs.
- It never bypasses login, CAPTCHA, or anti-bot systems.
- It only traverses up to `BACKFILL_MAX_PAGES` per search.
- It fetches detail pages only for jobs that are unseen or not yet processed.
- It caps processing at `BACKFILL_MAX_DETAIL_FETCHES`.

## LinkedIn Job Alerts Via Email

Create LinkedIn Job Alerts in LinkedIn and let the alert emails arrive in a mailbox the agent can read over IMAP. The agent parses job cards/links from email HTML or plaintext and normalizes them into the same pipeline as FINN:

`fetch -> parse -> dedup -> hardfilter -> AI-score -> Telegram`

For Gmail:

1. Enable 2-step verification on the Google account.
2. Create a Gmail app password.
3. Use `imap.gmail.com`, port `993`, your email address as `EMAIL_USERNAME`, and the app password as `EMAIL_PASSWORD`.
4. Use `EMAIL_FROM_FILTER=linkedin` if LinkedIn uses multiple sender addresses.

By default, the email reader opens the mailbox read-only, fetches messages with `BODY.PEEK[]`, and does not mark emails as read, delete them, or move them. Parsed LinkedIn jobs use `source=linkedin_email` and dedupe on the canonical LinkedIn job URL.

Set `EMAIL_POST_PROCESS_ACTION=archive` to remove parsed LinkedIn alert emails from the inbox after the jobs have been extracted. Set `EMAIL_POST_PROCESS_ACTION=trash` only if you want those parsed alert emails moved to trash. FINN listings cannot be deleted from FINN; the agent marks them as seen/processed in SQLite instead.

If LinkedIn alerts are forwarded from Outlook/Hotmail into Gmail, set `EMAIL_FROM_FILTER` to the forwarding address and leave `EMAIL_SUBJECT_FILTER` empty. Forwarded subjects may look like `Vs:` or `Fwd:` and do not always contain the word `job`.

## Telegram Setup

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`, choose a name and username, and copy the bot token.
3. Start a chat with your new bot and send any message.
4. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
5. Run:

```bash
python scripts/telegram_setup.py --write-env
```

`TELEGRAM_CHAT_ID` must be the numeric chat id, not a `t.me` URL or `@handle`.

## GitHub Actions Setup

Required repository secrets:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `FINN_SEARCH_URLS`

Additional secrets if email ingestion is enabled:

- `EMAIL_USERNAME`
- `EMAIL_PASSWORD`

Optional repository variables:

- `MIN_SCORE`
- `REQUEST_DELAY_SECONDS`
- `MAX_DETAIL_FETCHES_PER_RUN`
- `MAX_NEW_JOBS_PER_RUN`
- `INITIAL_BACKFILL`
- `BACKFILL_MAX_PAGES`
- `BACKFILL_MAX_DETAIL_FETCHES`
- `DRY_RUN`
- `OPENAI_MODEL`
- `FINN_MAX_PAGES_PER_SEARCH`
- `ENABLE_EMAIL_INGESTION`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_FOLDER`
- `EMAIL_FROM_FILTER`
- `EMAIL_SUBJECT_FILTER`
- `EMAIL_LOOKBACK_DAYS`
- `MAX_EMAILS_PER_RUN`
- `EMAIL_POST_PROCESS_ACTION`

The workflow runs at `06:15` and `18:15` UTC and can also be started manually from the Actions tab. The SQLite database is cached between workflow runs using `actions/cache`.

When `DRY_RUN=true`, the agent uses a separate `*.dry-run.sqlite` database so test runs do not consume jobs from the real alert database.

## Scoring Behavior

The AI returns strict JSON:

```json
{
  "score": 0,
  "recommendation": "SØK",
  "career_move_type": "STEP_UP",
  "headhunter_verdict": "",
  "why_relevant": "",
  "red_flags": "",
  "mandate_assessment": "",
  "level_assessment": "",
  "salary_potential": "",
  "application_angle": "",
  "confidence": "HIGH"
}
```

Scoring weights:

- 20% role step-up from Logistics Manager.
- 20% mandate and influence.
- 15% profile match in supply chain, logistics, planning, S&OP, SAP/Relex, export, and manufacturing/FMCG.
- 15% transformation, operational excellence, and AI potential.
- 15% compensation and career upside.
- 10% industry relevance.
- 5% geography and hybrid fit.

The scorer penalizes low-upside lateral moves, pure operational firefighting, coordinator/specialist roles without strategic scope, and roles likely below current compensation. It rewards Director/Head/Senior Manager scope, S&OP/IBP, planning excellence, transformation, automation, SAP/S4, and supply chain strategy.

## Reliability Notes

- HTTP requests use retries for transient errors.
- FINN detail pages are fetched only for new FINN listings.
- LinkedIn email jobs come from email content only; no LinkedIn pages are fetched.
- The run continues on partial failures.
- Telegram sends are skipped in `DRY_RUN=true`.
- Logs include FINN jobs fetched, LinkedIn emails scanned, LinkedIn jobs parsed, archived/trashed email counts, new jobs after dedup, hardfilter counts, scored jobs, and Telegram alerts.

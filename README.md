# Personal Job Search Agent

Private Python job-search monitor for Simen Eriksen Fricker. It checks saved FINN job searches and optional LinkedIn Job Alert emails, scores new listings with OpenAI, and sends only high-quality matches to Telegram.

LinkedIn is not scraped directly. LinkedIn jobs are ingested only from Job Alert emails in your mailbox.

## What It Does

- Fetches the first 1-3 pages from saved FINN job-search URLs.
- Optionally reads Gmail job-alert emails through the Gmail API.
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
├── scripts/gmail_setup.py
└── src
    ├── main.py
    ├── fetch_finn.py
    ├── fetch_gmail.py
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

To include Gmail job alerts locally:

```bash
DRY_RUN=true ENABLE_GMAIL=true python src/main.py
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

Optional Gmail job-alert ingestion:

- `ENABLE_GMAIL`: `true` to read job-alert emails from Gmail. Default: `false`.
- `GMAIL_CREDENTIALS_PATH`: OAuth client credentials JSON path. Default: `secrets/gmail_credentials.json`.
- `GMAIL_TOKEN_PATH`: OAuth user token JSON path. Default: `secrets/gmail_token.json`.
- `GMAIL_QUERY`: Gmail search query. Default: `in:inbox from:(linkedin.com OR finn.no OR indeed.com) newer_than:14d`.
- `GMAIL_CLEANUP_ACTION`: Post-processing action for successfully processed alert emails. Options: `none`, `archive`, `trash`. Default: `archive`.
- `GMAIL_MAX_EMAILS_PER_RUN`: Max Gmail messages scanned per run. Default: `20`.

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

## Gmail Job Alerts

Create LinkedIn, FINN, Indeed, or similar job alerts and let those emails arrive in Gmail. The agent uses the Gmail API to parse job cards/links from email HTML or plaintext and normalizes them into the same pipeline as FINN:

`fetch -> parse -> dedup -> hardfilter -> AI-score -> Telegram`

LinkedIn is not scraped directly. Only LinkedIn alert emails from your Gmail are parsed.

Gmail API setup:

1. Create a Google Cloud OAuth desktop/client credential with Gmail API enabled.
2. Install dependencies with `pip install -r requirements.txt`.
3. Generate a user OAuth token locally:

```bash
python scripts/gmail_setup.py
```

Use this alternative if the machine cannot open a browser automatically:

```bash
python scripts/gmail_setup.py --no-browser
```

The script uses the `https://www.googleapis.com/auth/gmail.modify` scope.
4. Save the OAuth client file as `secrets/gmail_credentials.json`, or point `GMAIL_CREDENTIALS_PATH` elsewhere before running the script.
5. Keep `secrets/` out of git. It is ignored by `.gitignore`.

Cleanup options:

- `GMAIL_CLEANUP_ACTION=none`: leave processed messages untouched.
- `GMAIL_CLEANUP_ACTION=archive`: remove the `INBOX` label after every job in the message was scored below `MIN_SCORE`. This is the recommended safe default.
- `GMAIL_CLEANUP_ACTION=trash`: move below-threshold processed messages to Gmail Trash. Use only when explicitly wanted.

The agent never cleans up unread/unprocessed messages just because they matched the query. A Gmail message is archived or trashed only after it was fetched, job links were extracted, every job has a saved score, every score is below `MIN_SCORE`, and no fatal error happened for that email. If one job in the message meets the alert threshold, the message is marked processed in SQLite but left in the inbox for human follow-up. Messages with parse/scoring errors or pending unscored jobs are also left in Gmail.

`DRY_RUN=true` also leaves Gmail messages untouched. The log records what cleanup action would have happened.

Parsed Gmail sources are stored as `gmail_linkedin`, `gmail_finn`, or `gmail_other` and deduped by canonical URL where possible.

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

Additional secrets if Gmail ingestion is enabled:

- `GMAIL_CREDENTIALS_JSON`: contents of `gmail_credentials.json`.
- `GMAIL_TOKEN_JSON`: contents of `gmail_token.json`.

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
- `ENABLE_GMAIL`
- `GMAIL_CREDENTIALS_PATH`
- `GMAIL_TOKEN_PATH`
- `GMAIL_QUERY`
- `GMAIL_CLEANUP_ACTION`
- `GMAIL_MAX_EMAILS_PER_RUN`

The workflow runs at `06:15` and `18:15` UTC and can also be started manually from the Actions tab. Open GitHub, go to **Actions**, select **Personal Job Search Agent**, click **Run workflow**, and choose the branch. The SQLite database is cached between workflow runs using `actions/cache`.

If `ENABLE_GMAIL=false`, missing Gmail credential/token secrets do not fail the run. If `ENABLE_GMAIL=true` and either OAuth file is missing or invalid, the log explains which path is missing and continues with FINN.

## Debugging GitHub Actions

When a scheduled run fails, open the failed workflow run and expand **Run job agent**. The agent logs:

- current working directory and Python version
- safe config snapshot with secret presence as booleans only
- enabled sources: FINN and Gmail
- FINN listings fetched
- Gmail emails found, processed, skipped, archived, and trashed
- jobs scored and Telegram alerts sent

Common fixes:

- Missing required secrets: `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, or `FINN_SEARCH_URLS`.
- Gmail enabled without `GMAIL_CREDENTIALS_JSON` and `GMAIL_TOKEN_JSON`.
- Bad `TELEGRAM_CHAT_ID`: it must be numeric.
- Invalid `GMAIL_CLEANUP_ACTION`: use `none`, `archive`, or `trash`.
- Database cache issues: rerun manually; `data/jobs.sqlite` is recreated if absent.

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
- Gmail jobs come from email content only; no LinkedIn pages are fetched.
- The run continues on partial failures.
- Telegram sends are skipped in `DRY_RUN=true`.
- Logs include FINN jobs fetched, Gmail emails found/processed/skipped, Gmail jobs parsed, archived/trashed email counts, new jobs after dedup, hardfilter counts, scored jobs, and Telegram alerts.

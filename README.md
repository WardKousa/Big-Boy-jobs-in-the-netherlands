# NL Job Tracker

Notifies you when a **new** job opens at top-tier companies in the Netherlands.
Instead of scraping fragile career-page HTML, it reads the public JSON endpoints
of the hiring platforms (ATS) those companies use — Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee, Workday.

Currently tracking **34 companies** across quant trading, big data/AI platforms,
fintech, and more (see `config/companies.yaml`).

## How it works

```
GitHub Actions (cron)  →  fetch each company's ATS JSON
                       →  filter by title keywords + NL location
                       →  diff against state/seen.json
                       →  notify only on NEW postings (console/Telegram/email)
```

## Quick start (local)

```bash
pip install -r requirements.txt

# See what currently matches your filters (no state written, no alerts):
python -m jobtracker.run --dry-run

# First real run: mark everything currently open as "already seen"
# so you only get alerted about jobs posted AFTER this moment:
python -m jobtracker.run --seed

# From now on, this prints only newly-appeared jobs and records them:
python -m jobtracker.run
```

## Configuration

- **`config/companies.yaml`** — the companies and their ATS mapping.
  Add one by finding its ATS (open its careers page, check the network tab)
  and adding an entry. The backlog at the bottom lists companies from the
  original wishlist that use custom systems still needing an adapter.
- **`config/settings.yaml`** — your match rules:
  - `include_keywords` — job titles must contain one of these
  - `exclude_keywords` — seniority filter (senior/lead/etc.)
  - `locations` — NL cities + `netherlands`
  - `notifications.channel` — `console` | `telegram` | `email`

## Notifications

Secrets are read from **environment variables**, never hardcoded.

**Telegram (recommended):**
1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Message your new bot once, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id.
3. Set `channel: telegram` in `settings.yaml` and export:
   ```bash
   export TELEGRAM_BOT_TOKEN=...   # or set in your shell / GH secrets
   export TELEGRAM_CHAT_ID=...
   ```

**Email:** set `channel: email` and export `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASS`, `EMAIL_TO` (for Gmail, use an App Password).

## Running for free on GitHub Actions

1. Push this repo to GitHub.
2. Add your secrets under **Settings → Secrets and variables → Actions**
   (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, …).
3. Set `channel` in `settings.yaml` to your channel and commit.
4. The workflow in `.github/workflows/check-jobs.yml` runs every 3 hours,
   commits updated state back to the repo, and pings you on new jobs.
   Trigger it manually anytime from the **Actions** tab.

Cost: **€0**. Public ATS endpoints + GitHub Actions free tier + Telegram.

## Adding more companies

Re-run discovery after editing the candidate lists in the scripts:

```bash
python scripts/discover_companies.py   # Greenhouse/Lever/Ashby/SR/Recruitee
python scripts/discover_workday.py     # Workday tenants
```

Whatever it finds, copy into `config/companies.yaml`.

## Tests

```bash
python tests/test_filters.py
```

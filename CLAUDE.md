# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt

python -m jobtracker.run --dry-run      # print matches, write no state, no alerts
python -m jobtracker.run --seed         # mark all current matches seen, no alerts (first-time setup)
python -m jobtracker.run --test-notify  # send one fake alert through the configured channel
python -m jobtracker.run                # normal: alert on new jobs only, save state

python tests/test_filters.py         # tests (self-contained runner, no pytest needed)
pytest tests/test_filters.py -k intern   # pytest also works for a single test
```

All commands must run from the repo root: `config.CONFIG_DIR` and
`state.DEFAULT_PATH` are relative paths (`config/`, `state/seen.json`).

## Architecture

Pipeline in `jobtracker/run.py`: load config → fetch all companies in parallel
(10-thread pool) → filter → diff against seen state → notify.

Dependencies are deliberately minimal — PyYAML only. All HTTP goes through
`urllib` in `adapters.py`. Keep it that way; the point is zero-cost operation on
the GitHub Actions free tier.

**`adapters.py`** — one `fetch_*` per ATS, registered in the `ADAPTERS` dict and
dispatched by the `ats` key in `config/companies.yaml`. Every adapter returns
normalized dicts via `_norm()` (`source_id`, `title`, `location`, `url`);
`fetch_company` then stamps on `company`, `ats`, and `tier`. Adding a provider
means writing a `fetch_*`, adding one `ADAPTERS` entry, and adding company
entries to the YAML. Network failures raise `FetchError`, which `fetch_all`
collects as warnings — one dead endpoint never fails the run. `fetch_all` also
contains *unexpected* exceptions per company (a provider silently changing its
JSON shape yields `KeyError`/`IndexError`), so one bad adapter can't drop the
other 40+ healthy companies.

Paginated adapters (Workday, Eightfold, SmartRecruiters, Amazon) are capped
(`MAX_WORKDAY_PAGES`, `MAX_EIGHTFOLD`). This is safe because results are
newest-first — verified against Nvidia, where offset 0 is "Posted Today" and
offset 1000 is "Posted 30+ Days Ago".

Several companies have bespoke adapters found by reverse-engineering their
sites. **ING** uses the reusable `radancy` adapter: Radancy's
`/en/search-jobs/results` endpoint answers plain GETs with JSON whose values
are server-rendered HTML fragments, so the adapter parses markup
(`RADANCY_ITEM_RE`) and must `html.unescape` titles. The others:
**Optiver** (own API at `www.optiver.com/en/api/v1/jobs`, Elasticsearch-style
`from`/`size` paging, `size` silently capped at 16 — the endpoint is named in
the careers page HTML as `apiEndpoint`); **Booking.com** via the reusable
`jibe` adapter (`jobs.booking.com/api/jobs`, plain GET, `limit=100` honoured);
**Tesla** (`fetch_tesla`), which must drive **headless Firefox** via
Playwright: Akamai 403s every plain HTTP client, flags headless Chromium by
its "HeadlessChrome" UA/client-hint brand, and flags even headed
Playwright-Chromium by its automation fingerprint — Firefox passes. Don't
fetch Tesla's state endpoint yourself from page JS either; only the SPA's own
request carries the Akamai sensor data, so the adapter captures that response
via `page.expect_response`. Without Playwright installed, Tesla degrades to a
FetchError warning. CI installs `playwright` + `firefox` as a separate
workflow step; `requirements.txt` stays PyYAML-only.

Workday has a trap worth knowing: **several tenants report `total` only on the
first page and `0` on every page after it** (Nvidia, Philips, NXP, eBay do;
ASML doesn't). Re-reading `total` each page therefore made `offset >= total`
fire on page 2 and silently capped those tenants at 40 jobs out of hundreds —
no error, just missing jobs. `fetch_workday` keeps the first non-zero `total`.
The page size cannot be raised; the cxs API rejects `limit > 20`. Server-side
location facets exist but the ids are tenant-specific (Philips' Netherlands
GUID 404s on NXP), so they aren't worth the fragility. Full pagination is why
a run takes ~4 minutes rather than ~5 seconds; for a 3-hourly cron that's a
good trade for not being blind to whole companies.

**`filters.py`** — whole-word regex matching (`\bkeyword\b`), so "intern" doesn't
match "international" and "lead" doesn't match "leading". Preserve this when
touching the matcher; several tests pin exactly these cases. Note the flip side:
`intern` also does not match `internship`, so both forms must be listed.

A title qualifies two ways: an `include_keywords` hit on its own, or an
`early_career_keywords` hit **paired with** a `technical_keywords` hit. The
pairing exists because a bare "intern" matches every HR/Law/Media internship a
large employer posts. Keep `technical_keywords` unambiguous — `it` matches the
pronoun in "make it happen" and re-admits the whole flood; a test pins this.

**`state.py`** — `state/seen.json` maps `"company::source_id"` → first-seen ISO
timestamp. Note `split_new(jobs, seen)` **mutates `seen` in place** while
returning the new jobs; `--seed` depends on that mutation, calling `save(seen)`
without notifying. Persistence is what makes "new" meaningful, and CI commits
the updated file back to the repo after each run.

**`notify.py`** — channel picked by `notifications.channel` in settings.yaml
(`console` | `telegram` | `email`), dispatched via the `NOTIFIERS` dict. Alerts
sort by `TIER_ORDER` (S++ → C, unknown last) so the best openings lead. Telegram
messages are chunked under the 4096-char API cap. Secrets come from env vars
only (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_*`, `EMAIL_TO`) — the
`enabled` flags in settings.yaml are vestigial and not read by the code.
`TELEGRAM_CHAT_ID` is a comma-separated recipient list; pressing Start on a
bot subscribes nobody, so every chat that should get alerts must be listed.
Failures name recipients by position ("recipient #2 of 2"), never by id —
CI logs are public.

Delivery failures raise `NotifyError`. Always surface the provider's own
message: urllib's `str(HTTPError)` is only "HTTP Error 400: Bad Request", while
the response body says "chat not found" — `_http_detail` reads it. Losing that
detail once cost a full debugging cycle on a CI failure.

The send path is only exercised when a real posting appears, which historically
meant a bad chat id stayed invisible for hours and then failed at the worst
moment. `--test-notify` exists to exercise it on demand; use it after any secret
change.

## Config

`config/companies.yaml` — company → ATS mapping plus a `tier` (S++ … C) used
only for alert ordering. Required keys vary by adapter: `slug` for
Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee; `tenant`/`wd`/`site` for
Workday; `host`/`domain`/`portal` for Eightfold. A backlog at the bottom of the
file lists wishlist companies whose custom systems still need adapters.

`config/settings.yaml` — `include_keywords` (title must contain one),
`exclude_keywords` (seniority filter), `locations`. Bare `remote` is
intentionally absent from locations; it would match "Remote - United States",
while NL-remote roles still match through "netherlands".

`scripts/discover_companies.py` and `scripts/discover_workday.py` probe
candidate slugs/tenants to find working endpoints; paste results into
companies.yaml. Their JSON output is gitignored.

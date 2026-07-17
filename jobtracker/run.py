"""
Entry point: fetch all companies, filter, diff against seen state, notify.

Usage:
  python -m jobtracker.run              # normal run
  python -m jobtracker.run --dry-run    # don't write state, print everything
  python -m jobtracker.run --seed       # mark all current matches as seen
                                        # (no alerts) -- run once on first setup
"""

import argparse
import concurrent.futures
import sys

from . import adapters, config, filters, notify, state

# Windows terminals default to a legacy codepage that mangles •/— etc.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def fetch_all(companies):
    """Fetch every company in parallel; collect jobs and errors.

    Every per-company failure is contained. Beyond FetchError, an adapter can
    raise anything when a provider quietly changes its JSON shape (KeyError,
    IndexError, TypeError); letting those escape would drop 40+ healthy
    companies on the floor because one endpoint hiccuped.
    """
    jobs, errors = [], []

    def _one(cfg):
        return cfg["name"], adapters.fetch_company(cfg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_one, c): c for c in companies}
        for fut in concurrent.futures.as_completed(futures):
            cfg = futures[fut]
            try:
                _, company_jobs = fut.result()
                jobs.extend(company_jobs)
            except adapters.FetchError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001 -- one bad adapter != dead run
                errors.append(
                    f"{cfg.get('name', '?')} [{cfg.get('ats', '?')}] "
                    f"unexpected {type(exc).__name__}: {exc}")
    return jobs, errors


def _test_notify(settings):
    """Push one fake job through the configured channel.

    The delivery path is otherwise only exercised when a real posting appears,
    so a bad token or chat id stays invisible until the moment it matters.
    """
    channel = (settings.get("notifications") or {}).get("channel", "console")
    fake = [{
        "company": "Test Company", "source_id": "test-1", "tier": "S++",
        "title": "Test Alert -- your job tracker is wired up correctly",
        "location": "Amsterdam, Netherlands",
        "url": "https://github.com/WardKousa/Big-Boy-jobs-in-the-netherlands",
    }]
    print(f"Sending a test alert via '{channel}'...")
    try:
        notify.notify(fake, settings)
    except notify.NotifyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"Test alert sent via '{channel}'. Check that you received it.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Job opening tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't persist state; print matches")
    parser.add_argument("--seed", action="store_true",
                        help="mark all current matches as seen without alerting")
    parser.add_argument("--test-notify", action="store_true",
                        help="send one fake job through the configured channel "
                             "and exit; verifies secrets without waiting for a "
                             "real posting")
    args = parser.parse_args(argv)

    settings = config.load_settings()
    if args.test_notify:
        return _test_notify(settings)

    companies = config.load_companies()
    flt = settings.get("filters", {})

    print(f"Checking {len(companies)} companies...")
    jobs, errors = fetch_all(companies)
    print(f"Fetched {len(jobs)} total postings.")
    for err in errors:
        print(f"  [warn] fetch failed: {err}", file=sys.stderr)

    matched = filters.apply_filters(jobs, flt)
    print(f"{len(matched)} match your filters.")

    seen = state.load()
    new_jobs = state.split_new(matched, seen)

    if args.seed:
        state.save(seen)
        print(f"Seeded {len(matched)} matches as already-seen. No alerts sent.")
        return 0

    if args.dry_run:
        notify.notify_console(new_jobs, settings)
        print("\n(dry-run: state not written)")
        return 0

    try:
        notify.notify(new_jobs, settings)
    except notify.NotifyError as exc:
        # State is deliberately NOT saved: these jobs stay unseen so the next
        # run re-sends them once delivery is fixed. Nothing is lost, and the
        # listings still reach the CI log below.
        print(f"\n[error] {exc}", file=sys.stderr)
        if new_jobs:
            print("\nDelivery failed -- the jobs it would have sent:")
            notify.notify_console(new_jobs, settings)
        print("\nState not saved; these will be re-sent on the next run.",
              file=sys.stderr)
        return 1

    state.save(seen)
    print(f"\n{len(new_jobs)} new since last run. State saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    """Fetch every company in parallel; collect jobs and errors."""
    jobs, errors = [], []

    def _one(cfg):
        return cfg, adapters.fetch_company(cfg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(_one, c) for c in companies]
        for fut in concurrent.futures.as_completed(futures):
            try:
                cfg, company_jobs = fut.result()
                jobs.extend(company_jobs)
            except adapters.FetchError as exc:
                errors.append(str(exc))
    return jobs, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Job opening tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't persist state; print matches")
    parser.add_argument("--seed", action="store_true",
                        help="mark all current matches as seen without alerting")
    args = parser.parse_args(argv)

    companies = config.load_companies()
    settings = config.load_settings()
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

    notify.notify(new_jobs, settings)
    state.save(seen)
    print(f"\n{len(new_jobs)} new since last run. State saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

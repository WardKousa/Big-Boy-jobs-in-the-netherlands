"""Unit tests for the matching logic (no network)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobtracker import filters, state  # noqa: E402

FILTERS = {
    "include_keywords": ["data engineer", "machine learning"],
    "exclude_keywords": ["senior", "lead"],
    "locations": ["netherlands", "amsterdam"],
}


def _job(title, location):
    return {"company": "X", "source_id": "1", "title": title,
            "location": location, "url": "u"}


def test_matches_included_keyword_and_location():
    job = _job("Data Engineer", "Amsterdam, Netherlands")
    assert filters.matches(job, FILTERS) is True


def test_rejects_when_title_keyword_absent():
    job = _job("Product Manager", "Amsterdam")
    assert filters.matches(job, FILTERS) is False


def test_rejects_excluded_seniority():
    job = _job("Senior Machine Learning Engineer", "Amsterdam")
    assert filters.matches(job, FILTERS) is False


def test_rejects_wrong_location():
    job = _job("Data Engineer", "Remote - United States")
    assert filters.matches(job, FILTERS) is False


def test_nl_remote_still_matches_via_country_name():
    job = _job("Machine Learning Engineer", "Remote - The Netherlands")
    assert filters.matches(job, FILTERS) is True


def test_empty_locations_accepts_any_place():
    flt = {"include_keywords": ["data engineer"], "locations": []}
    job = _job("Data Engineer", "Tokyo, Japan")
    assert filters.matches(job, flt) is True


def test_split_new_only_returns_unseen():
    seen = {}
    jobs = [_job("Data Engineer", "Amsterdam")]
    first = state.split_new(jobs, seen)
    assert len(first) == 1
    second = state.split_new(jobs, seen)  # same job again
    assert len(second) == 0


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except AssertionError:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)

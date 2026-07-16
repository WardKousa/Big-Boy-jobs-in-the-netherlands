"""
Persistence of already-seen jobs so we only alert on genuinely new postings.

State is a JSON file keyed by "company::source_id" -> first-seen ISO timestamp.
"""

import json
import os
from datetime import datetime, timezone

DEFAULT_PATH = os.path.join("state", "seen.json")


def job_key(job):
    return f"{job['company']}::{job['source_id']}"


def load(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save(seen, path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False, sort_keys=True)


def split_new(jobs, seen):
    """Partition jobs into (new, seen_keys_touched) and mutate `seen`."""
    now = datetime.now(timezone.utc).isoformat()
    new_jobs = []
    for job in jobs:
        key = job_key(job)
        if key not in seen:
            seen[key] = now
            new_jobs.append(job)
    return new_jobs

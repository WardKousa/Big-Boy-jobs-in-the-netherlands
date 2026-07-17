"""Match jobs against include/exclude keyword and location rules."""

import re

_CACHE = {}


def _pattern(needle):
    """Whole-word, case-insensitive matcher for one keyword/phrase.

    Whole-word matching avoids false hits like "intern" inside "international"
    or "lead" inside "leading" -- important now that short tokens (intern,
    sre, quant, lead) are in the keyword lists.
    """
    pat = _CACHE.get(needle)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(needle.strip().lower()) + r"\b")
        _CACHE[needle] = pat
    return pat


def _contains_any(text, needles):
    text = text.lower()
    return any(n.strip() and _pattern(n).search(text) for n in needles)


def _title_wanted(title, filters):
    """Does the title describe a role we want?

    Two independent ways to qualify:

    1. `include_keywords` -- a role name that stands on its own
       ("data engineer"), matched directly.
    2. `early_career_keywords` + `technical_keywords` -- an early-career term
       ("intern") only qualifies alongside a technical signal. On its own,
       "intern" matches every HR, Law and Media internship a big employer
       posts, which drowns the alerts that matter. Both lists must be
       configured for this path; otherwise it is skipped and behaviour is
       exactly the legacy include-only match.
    """
    include = filters.get("include_keywords") or []
    if include and _contains_any(title, include):
        return True

    early = filters.get("early_career_keywords") or []
    technical = filters.get("technical_keywords") or []
    if early and technical:
        if _contains_any(title, early) and _contains_any(title, technical):
            return True

    # No include list and no early-career pair configured => accept everything.
    return not include and not (early and technical)


def matches(job, filters):
    """Return True if a normalized job passes the configured filters."""
    title = job.get("title", "")
    location = job.get("location", "")

    if not _title_wanted(title, filters):
        return False

    exclude = filters.get("exclude_keywords") or []
    if exclude and _contains_any(title, exclude):
        return False

    locations = filters.get("locations") or []
    if locations and not _contains_any(location, locations):
        return False

    return True


def apply_filters(jobs, filters):
    return [j for j in jobs if matches(j, filters)]


def dedupe(jobs):
    """Collapse postings that differ only by requisition id.

    Big employers list one role under several reqs -- ASML posts the same
    "Computer Science internship: IT control framework" twice -- and alerting
    on each is noise. Apply this to the *new* jobs only, after state has
    recorded every source_id, so the dropped twin cannot resurface as new.
    """
    out, seen_keys = [], set()
    for job in jobs:
        key = (job.get("company", "").strip().lower(),
               job.get("title", "").strip().lower(),
               job.get("location", "").strip().lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(job)
    return out

"""Match jobs against include/exclude keyword and location rules."""


def _contains_any(text, needles):
    text = text.lower()
    return any(n.lower() in text for n in needles)


def matches(job, filters):
    """Return True if a normalized job passes the configured filters."""
    title = job.get("title", "")
    location = job.get("location", "")

    include = filters.get("include_keywords") or []
    if include and not _contains_any(title, include):
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

"""
ATS adapters. Each fetch_* function hits a provider's public JSON endpoint and
returns a list of normalized job dicts:

    {
      "source_id": str,   # stable id unique within a company
      "title": str,
      "location": str,
      "url": str,
      "company": str,     # filled in by the dispatcher
      "ats": str,
    }

Stdlib only (urllib) so the tracker runs with zero third-party HTTP deps.
"""

import json
import urllib.error
import urllib.request

TIMEOUT = 20
UA = "Mozilla/5.0 (compatible; job-tracker/1.0; +https://github.com)"


class FetchError(Exception):
    """Raised when a provider endpoint cannot be read."""


def _get_json(url, data=None, method="GET"):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError, ConnectionError) as exc:
        raise FetchError(f"{url}: {exc}") from exc


def _norm(source_id, title, location, url):
    return {
        "source_id": str(source_id),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
    }


def fetch_greenhouse(cfg):
    slug = cfg["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(_norm(j.get("id"), j.get("title"), loc, j.get("absolute_url")))
    return out


def fetch_lever(cfg):
    slug = cfg["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get_json(url)
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "")
        out.append(_norm(j.get("id"), j.get("text"), loc, j.get("hostedUrl")))
    return out


def fetch_ashby(cfg):
    slug = cfg["slug"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append(_norm(j.get("id"), j.get("title"),
                         j.get("location"), j.get("jobUrl")))
    return out


def fetch_smartrecruiters(cfg):
    slug = cfg["slug"]
    out = []
    offset = 0
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
               f"?limit=100&offset={offset}")
        data = _get_json(url)
        content = data.get("content", []) if isinstance(data, dict) else []
        for j in content:
            loc = j.get("location") or {}
            loc_str = ", ".join(
                x for x in [loc.get("city"), loc.get("country")] if x)
            job_url = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
            out.append(_norm(j.get("id"), j.get("name"), loc_str, job_url))
        total = data.get("totalFound", 0) if isinstance(data, dict) else 0
        offset += 100
        if offset >= total or not content:
            break
    return out


def fetch_recruitee(cfg):
    slug = cfg["slug"]
    url = f"https://{slug}.recruitee.com/api/offers/"
    data = _get_json(url)
    out = []
    for j in data.get("offers", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        job_url = j.get("careers_url") or j.get("careers_apply_url", "")
        out.append(_norm(j.get("id"), j.get("title"), loc, job_url))
    return out


def fetch_workday(cfg):
    tenant, wd, site = cfg["tenant"], cfg["wd"], cfg["site"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out = []
    offset = 0
    while True:
        body = {"limit": 20, "offset": offset, "searchText": ""}
        data = _get_json(api, data=body, method="POST")
        postings = data.get("jobPostings", []) if isinstance(data, dict) else []
        for j in postings:
            path = j.get("externalPath", "")
            job_url = f"{base}/{site}{path}" if path else base
            loc = j.get("locationsText", "")
            out.append(_norm(j.get("bulletFields", [path])[0] or path,
                             j.get("title"), loc, job_url))
        total = data.get("total", 0) if isinstance(data, dict) else 0
        offset += 20
        if offset >= total or not postings:
            break
    return out


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workday": fetch_workday,
}


def fetch_company(cfg):
    """Fetch + normalize all jobs for one company config entry."""
    ats = cfg.get("ats")
    fn = ADAPTERS.get(ats)
    if fn is None:
        raise FetchError(f"unknown ats '{ats}' for {cfg.get('name')}")
    jobs = fn(cfg)
    for j in jobs:
        j["company"] = cfg["name"]
        j["ats"] = ats
    return jobs

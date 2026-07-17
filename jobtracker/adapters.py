"""
ATS adapters. Each fetch_* function hits a provider's public JSON endpoint and
returns a list of normalized job dicts:

    {
      "source_id": str,   # stable id unique within a company
      "title": str,
      "location": str,
      "url": str,
      "posted": str,      # human age ("today", "12d ago"); "" if unknown
      "company": str,     # filled in by the dispatcher
      "ats": str,
    }

Note "posted" is when the provider says the role was published -- not when this
tracker first saw it. A job can be new to us (a company was just added, or a
paging bug was fixed) while being months old.

Stdlib only (urllib) so the tracker runs with zero third-party HTTP deps.
"""

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Workday/Eightfold tenants can hold thousands of jobs. Results are newest-first,
# so capping still catches anything newly posted between runs while bounding time.
WORKDAY_PAGE_SIZE = 20      # the cxs API rejects anything larger
MAX_WORKDAY_PAGES = 110     # 110 * 20 = up to 2200 jobs (Nvidia reports ~2000)
MAX_EIGHTFOLD = 1000


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


def _age_from(dt):
    """Render a posting datetime as a short age, e.g. "today" / "12d ago"."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    if days < 0:
        return ""
    if days == 0:
        return "today"
    if days == 1:
        return "1d ago"
    return f"{days}d ago"


def _posted_str(value):
    """Best-effort age string from whatever shape a provider reports.

    Every ATS answers this question differently -- epoch millis (Lever), ISO
    with an offset (Greenhouse/Ashby), ISO with Z (SmartRecruiters), a
    space-separated UTC stamp (Recruitee), a long-form date (Amazon), or
    already-humanised prose (Workday's "Posted Today"). Best-effort by design:
    an unparseable value yields "", and the alert simply omits the age rather
    than failing.
    """
    if value in (None, ""):
        return ""

    if isinstance(value, (int, float)):
        # Lever reports milliseconds; anything past ~2001 in seconds is millis.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return _age_from(datetime.fromtimestamp(seconds, timezone.utc))
        except (OverflowError, OSError, ValueError):
            return ""

    text = str(value).strip()
    if not text:
        return ""

    # Workday already says it in words: "Posted Today", "Posted 30+ Days Ago".
    if text.lower().startswith("posted"):
        return text[len("posted"):].strip().lower()

    cleaned = re.sub(r"\s+", " ", text)
    candidates = [cleaned]
    if cleaned.endswith(" UTC"):                      # Recruitee
        candidates.append(cleaned[:-4].replace(" ", "T"))
    if cleaned.endswith("Z"):                         # SmartRecruiters
        candidates.append(cleaned[:-1] + "+00:00")

    for candidate in candidates:
        try:
            return _age_from(datetime.fromisoformat(candidate))
        except ValueError:
            pass
    try:                                              # Amazon: "November 3, 2025"
        return _age_from(datetime.strptime(cleaned, "%B %d, %Y"))
    except ValueError:
        return ""


def _norm(source_id, title, location, url, posted=None):
    return {
        "source_id": str(source_id),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "posted": _posted_str(posted),
    }


def fetch_greenhouse(cfg):
    slug = cfg["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(_norm(j.get("id"), j.get("title"), loc,
                         j.get("absolute_url"), j.get("updated_at")))
    return out


def fetch_lever(cfg):
    slug = cfg["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get_json(url)
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "")
        out.append(_norm(j.get("id"), j.get("text"), loc,
                         j.get("hostedUrl"), j.get("createdAt")))
    return out


def fetch_ashby(cfg):
    slug = cfg["slug"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append(_norm(j.get("id"), j.get("title"), j.get("location"),
                         j.get("jobUrl"), j.get("publishedAt")))
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
            out.append(_norm(j.get("id"), j.get("name"), loc_str, job_url,
                             j.get("releasedDate")))
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
        out.append(_norm(j.get("id"), j.get("title"), loc, job_url,
                         j.get("published_at")))
    return out


def fetch_workday(cfg):
    tenant, wd, site = cfg["tenant"], cfg["wd"], cfg["site"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    max_pages = cfg.get("max_pages", MAX_WORKDAY_PAGES)
    out = []
    offset = 0
    total = 0
    for _ in range(max_pages):
        body = {"limit": WORKDAY_PAGE_SIZE, "offset": offset, "searchText": ""}
        data = _get_json(api, data=body, method="POST")
        postings = data.get("jobPostings", []) if isinstance(data, dict) else []
        for j in postings:
            path = j.get("externalPath", "")
            job_url = f"{base}/{site}{path}" if path else base
            loc = j.get("locationsText", "")
            # bulletFields holds the requisition id, but a tenant can return it
            # empty -- the dict default alone doesn't cover that, and [0] on an
            # empty list would take down the whole run.
            bullets = j.get("bulletFields") or []
            out.append(_norm((bullets[0] if bullets else "") or path,
                             j.get("title"), loc, job_url,
                             j.get("postedOn")))

        # Nvidia, Philips, NXP and eBay report `total` only on the first page
        # and 0 on every page after it. Re-reading it each time made
        # "offset >= total" fire on page 2, capping every such tenant at 40
        # jobs out of hundreds. Keep the first non-zero value we are given.
        page_total = (data.get("total") or 0) if isinstance(data, dict) else 0
        if page_total:
            total = max(total, page_total)

        offset += WORKDAY_PAGE_SIZE
        if not postings:
            break
        if total and offset >= total:
            break
    return out


def fetch_amazon(cfg):
    """amazon.jobs public search API, pre-filtered to the Netherlands."""
    out = []
    offset = 0
    while True:
        url = ("https://www.amazon.jobs/en/search.json?"
               "normalized_country_code%5B%5D=NLD&sort=recent"
               f"&result_limit=100&offset={offset}")
        data = _get_json(url)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for j in jobs:
            job_url = "https://www.amazon.jobs" + (j.get("job_path") or "")
            out.append(_norm(j.get("id_icims") or j.get("id"),
                             j.get("title"), j.get("location"), job_url,
                             j.get("posted_date")))
        hits = data.get("hits", 0) if isinstance(data, dict) else 0
        offset += 100
        if offset >= hits or not jobs:
            break
    return out


def fetch_atlassian(cfg):
    """Atlassian's iCIMS-backed public listings feed (all locations)."""
    url = "https://www.atlassian.com/endpoint/careers/listings"
    data = _get_json(url)
    out = []
    for j in data if isinstance(data, list) else []:
        loc = "; ".join(j.get("locations", []) or [])
        job_url = (j.get("portalJobPost") or {}).get("portalUrl", "")
        out.append(_norm(j.get("id"), j.get("title"), loc, job_url))
    return out


def fetch_snap(cfg):
    """careers.snap.com search API (all locations; filtered downstream)."""
    url = "https://careers.snap.com/api/jobs"
    data = _get_json(url)
    out = []
    for item in data.get("body", []) if isinstance(data, dict) else []:
        s = item.get("_source", {})
        loc = s.get("primary_location") or "; ".join(
            o.get("location", "") for o in s.get("offices", []))
        out.append(_norm(item.get("_id") or s.get("id"),
                         s.get("title"), loc, s.get("absolute_url")))
    return out


def fetch_eightfold(cfg):
    """Eightfold.ai talent-portal API (used by Netflix and others)."""
    host = cfg["host"]          # e.g. netflix.eightfold.ai
    domain = cfg["domain"]      # e.g. netflix.com
    portal = cfg["portal"]      # e.g. explore.jobs.netflix.net
    out = []
    start = 0
    while start < MAX_EIGHTFOLD:
        url = (f"https://{host}/api/apply/v2/jobs?domain={domain}"
               f"&start={start}&num=50&location=Netherlands")
        data = _get_json(url)
        positions = data.get("positions", []) if isinstance(data, dict) else []
        for p in positions:
            loc = p.get("location") or "; ".join(p.get("locations", []))
            job_url = f"https://{portal}/careers/job/{p.get('id')}"
            out.append(_norm(p.get("id"),
                             p.get("name") or p.get("posting_name"),
                             loc, job_url))
        count = data.get("count", 0) if isinstance(data, dict) else 0
        start += 50
        if start >= count or not positions:
            break
    return out


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workday": fetch_workday,
    "amazon": fetch_amazon,
    "atlassian": fetch_atlassian,
    "snap": fetch_snap,
    "eightfold": fetch_eightfold,
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
        j["tier"] = cfg.get("tier", "")
    return jobs

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

import html
import json
import re
import urllib.error
import urllib.parse
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
OPTIVER_PAGE_SIZE = 16      # the API caps `size` at 16 whatever you ask for
MAX_OPTIVER_PAGES = 40      # 40 * 16 = 640 (Optiver lists ~190 globally)
MAX_JIBE_PAGES = 30         # 30 * 100 = 3000 jobs per Jibe board
MAX_PCSX_RESULTS = 500      # pcsx pages are pinned at 10; NL counts are small
RADANCY_PAGE_SIZE = 100
MAX_RADANCY_PAGES = 30      # 30 * 100 = 3000 jobs (ING lists ~750 globally)

# One search-result item in Radancy's server-rendered list: the title anchor
# (carrying data-job-id) followed by its job-location span. The btn-icon
# anchors also carry data-job-id but contain no <h2>, so they can't match.
RADANCY_ITEM_RE = re.compile(
    r'<a href="(/[^"]*?/job/[^"]+)" data-job-id="(\d+)">\s*'
    r'<h2[^>]*>([^<]+)</h2>\s*</a>.*?'
    r'<span class="job-location">([^<]*)</span>', re.S)

MAX_GOOGLE_PAGES = 15
# Google server-renders NL results into the careers HTML as
# jobs/results/<numeric-id>-<title-slug>. The slug is the only reliably
# machine-readable title on the page.
GOOGLE_LINK_RE = re.compile(r'jobs/results/(\d+)-([a-z0-9-]+)')


class FetchError(Exception):
    """Raised when a provider endpoint cannot be read."""


def _get_text(url):
    headers = {"User-Agent": UA, "Accept": "text/html"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ConnectionError) as exc:
        raise FetchError(f"{url}: {exc}") from exc


def _get_json(url, data=None, method="GET", headers=None):
    headers = {"User-Agent": UA, "Accept": "application/json",
               **(headers or {})}
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


def fetch_phenom(cfg):
    """Phenom-powered career sites (Just Eat Takeaway, ABB).

    POST /widgets with ddoKey=refineSearch returns JSON job lists. The
    server-side country filter keeps giant multinationals to their NL
    postings. Job pages live at /global/<lang>/job/<jobId> on the same host.
    """
    host = cfg["host"]                  # e.g. careers.justeattakeaway.com
    lang = cfg.get("lang", "en")        # JET uses en_global, ABB en
    country = cfg.get("country", "Netherlands")
    lang_path = cfg.get("lang_path", "en")
    out = []
    offset = 0
    while offset < 1000:
        body = {"lang": lang, "deviceType": "desktop", "country": "global",
                "ddoKey": "refineSearch", "sortBy": "", "from": offset,
                "jobs": True, "counts": True,
                "all_fields": ["category", "country", "city"],
                "size": 50, "clearAll": False, "jdsource": "facets",
                "isSliderEnable": False, "pageName": "search-results",
                "keywords": "", "global": True,
                "selected_fields": {"country": [country]}, "locationData": {}}
        d = _get_json(f"https://{host}/widgets", data=body, method="POST")
        rs = d.get("refineSearch", {}) if isinstance(d, dict) else {}
        jobs = (rs.get("data") or {}).get("jobs") or []
        for j in jobs:
            loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
            jid = j.get("jobId")
            out.append(_norm(jid, j.get("title"), loc,
                             f"https://{host}/global/{lang_path}/job/{jid}",
                             j.get("postedDate")))
        total = rs.get("totalHits") or 0
        offset += 50
        if not jobs or offset >= total:
            break
    return out


def fetch_google(cfg):
    """Google careers, server-rendered so plain HTTP works.

    Results for a location are baked into the HTML as
    jobs/results/<id>-<title-slug>. There's no clean title element, so the
    slug is the title source (loses acronym casing -- "III" -> "Iii" -- but
    it's readable). Location isn't per-card in the markup; the page is already
    filtered to `query` so we label it that way for the location filter.
    """
    query = cfg.get("query", "Netherlands")
    label = cfg.get("location_label", query)
    base = ("https://www.google.com/about/careers/applications/jobs/results/"
            f"?location={urllib.parse.quote(query)}")
    out = []
    seen = set()
    for page in range(1, MAX_GOOGLE_PAGES + 1):
        htmltext = _get_text(f"{base}&page={page}")
        found = GOOGLE_LINK_RE.findall(htmltext)
        new = [(jid, slug) for jid, slug in found if jid not in seen]
        for jid, slug in new:
            seen.add(jid)
            title = slug.replace("-", " ").strip().title()
            out.append(_norm(
                jid, title, label,
                f"https://www.google.com/about/careers/applications/"
                f"jobs/results/{jid}-{slug}"))
        if not new:
            break
    return out


def fetch_uber(cfg):
    """Uber's careers API, pre-filtered to the Netherlands.

    The endpoint 403s without a CSRF header, but accepts the literal value
    "x" -- the check only tests presence. Results carry creationDate for age.
    """
    url = "https://www.uber.com/api/loadSearchJobsResults?localeCode=en"
    out = []
    page = 0
    while page < 20:
        body = {"params": {"location": [{"country": "NLD"}],
                           "page": page, "limit": 100}}
        d = _get_json(url, data=body, method="POST",
                      headers={"x-csrf-token": "x"})
        data = d.get("data", {}) if isinstance(d, dict) else {}
        results = data.get("results") or []
        for p in results:
            locs = "; ".join(
                ", ".join(x for x in [l.get("city"), l.get("countryName")] if x)
                for l in (p.get("allLocations") or []))
            out.append(_norm(p.get("id"), p.get("title"), locs,
                             f"https://www.uber.com/global/en/careers/list/{p.get('id')}/",
                             p.get("creationDate")))
        total = data.get("totalResults") or {}
        total_n = total.get("low", 0) if isinstance(total, dict) else total
        page += 1
        if not results or len(out) >= total_n:
            break
    return out


def fetch_pcsx(cfg):
    """Eightfold's newer "pcsx" frontend API (Microsoft's careers site).

    Plain GET, response nested under "data". Page size is fixed at 10 no
    matter what num= asks for, so paginate with start. postedTs is epoch
    seconds. The classic /api/apply/v2/jobs endpoint 403s on these hosts.
    """
    host = cfg["host"]          # e.g. apply.careers.microsoft.com
    domain = cfg["domain"]      # e.g. microsoft.com
    location = cfg.get("location", "")
    loc_q = urllib.parse.quote(location)
    out = []
    start = 0
    while start < MAX_PCSX_RESULTS:
        url = (f"https://{host}/api/pcsx/search?domain={domain}&query="
               f"&location={loc_q}&start={start}")
        data = (_get_json(url) or {}).get("data") or {}
        positions = data.get("positions") or []
        for p in positions:
            purl = p.get("positionUrl") or ""
            job_url = f"https://{host}{purl}" if purl.startswith("/") else purl
            out.append(_norm(p.get("id"), p.get("name"),
                             "; ".join(p.get("locations") or []),
                             job_url, p.get("postedTs")))
        count = data.get("count") or 0
        start += len(positions) or 10
        if not positions or start >= count:
            break
    return out


def fetch_radancy(cfg):
    """Radancy-powered career sites (ING's careers.ing.com).

    The search endpoint answers plain GETs with JSON, but the values are
    server-rendered HTML fragments -- so this parses markup, not an API
    schema. The structure (title anchor with data-job-id + job-location span)
    is what the site itself renders, which makes it as stable as the site.
    No total count is exposed; page until a short page.
    """
    host = cfg["host"]                      # e.g. careers.ing.com
    prefix = cfg.get("path", "/en")         # locale prefix of the search app
    out = []
    seen_ids = set()
    for page in range(1, MAX_RADANCY_PAGES + 1):
        url = (f"https://{host}{prefix}/search-jobs/results?ActiveFacetID=0"
               f"&CurrentPage={page}&RecordsPerPage={RADANCY_PAGE_SIZE}"
               "&SearchResultsModuleName=Search+Results"
               "&SearchFiltersModuleName=Search+Filters")
        data = _get_json(url)
        found = RADANCY_ITEM_RE.findall(
            data.get("results", "") if isinstance(data, dict) else "")
        for href, jid, title, loc in found:
            if jid in seen_ids:
                continue
            seen_ids.add(jid)
            out.append(_norm(jid, html.unescape(title),
                             html.unescape(loc), f"https://{host}{href}"))
        if len(found) < RADANCY_PAGE_SIZE:
            break
    return out


def fetch_jibe(cfg):
    """Jibe-powered career sites (Booking.com's jobs.booking.com).

    Plain GET JSON API at /api/jobs. `limit` raises the page size, but page
    through anyway in case a board outgrows one page. An optional `location`
    narrows server-side (Booking lists ~50 NL jobs vs thousands globally).
    """
    host = cfg["host"]                      # e.g. jobs.booking.com
    board = cfg.get("board", "booking")     # path segment of the job pages
    location = cfg.get("location", "")
    loc_param = f"&location={urllib.parse.quote(location)}" if location else ""
    out = []
    page = 1
    while page <= MAX_JIBE_PAGES:
        url = (f"https://{host}/api/jobs?page={page}&limit=100"
               f"&sortBy=relevance&descending=false&internal=false{loc_param}")
        data = _get_json(url)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for item in jobs:
            j = item.get("data") or {}
            slug = j.get("slug") or j.get("req_id")
            job_url = f"https://{host}/{board}/jobs/{slug}"
            out.append(_norm(j.get("req_id") or slug, j.get("title"),
                             j.get("full_location") or ", ".join(
                                 x for x in [j.get("city"), j.get("country")]
                                 if x),
                             job_url, j.get("posted_date")))
        total = data.get("totalCount", 0) if isinstance(data, dict) else 0
        if not jobs or len(out) >= total:
            break
        page += 1
    return out


def fetch_optiver(cfg):
    """Optiver's own jobs API, found embedded in the careers page as
    "apiEndpoint":"/en/api/v1/jobs".

    Paging is Elasticsearch-style from/size, and `size` is capped server-side
    at 16 no matter what you ask for -- so paging is mandatory, not optional.
    The API also accepts &location=amsterdam, but we fetch every location and
    let the normal filters decide, so the config stays declarative.
    """
    base = "https://www.optiver.com/en/api/v1/jobs"
    out = []
    start = 0
    total = 0
    for _ in range(MAX_OPTIVER_PAGES):
        data = _get_json(f"{base}?from={start}&size={OPTIVER_PAGE_SIZE}")
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            break
        for j in items:
            href = j.get("href") or ""
            job_url = f"https://www.optiver.com{href}" if href.startswith("/") \
                else href
            # href encodes department/office/slug and is stable across
            # re-publishes; componentID is not.
            out.append(_norm(href or j.get("componentID"), j.get("title"),
                             j.get("location"), job_url))
        total = total or (data.get("totalCount") or 0)
        start += OPTIVER_PAGE_SIZE
        if total and start >= total:
            break
    return out


def _tesla_jobs_from_state(state):
    """Normalize Tesla's /cua-api/apps/careers/state payload.

    Listings are compact ({t: title, l: location-id}); location ids resolve
    through lookup.locations to "City, Province" strings that never name the
    country. For NL ids (found by walking the geo tree for site "NL") we emit
    "City, Netherlands" instead, so the standard location filter matches every
    Dutch site -- including ones like Tilburg that aren't in the filter list
    by city name.
    """
    nl_city = {}
    for region in state.get("geo") or []:
        for site in region.get("sites") or []:
            if site.get("id") == "NL":
                for city, ids in (site.get("cities") or {}).items():
                    for lid in ids:
                        nl_city[str(lid)] = city
    locations = (state.get("lookup") or {}).get("locations") or {}
    out = []
    for j in state.get("listings") or []:
        lid = str(j.get("l", ""))
        if lid in nl_city:
            loc = f"{nl_city[lid]}, Netherlands"
        else:
            loc = locations.get(lid, "")
        jid = j.get("id")
        out.append(_norm(jid, j.get("t"), loc,
                         f"https://www.tesla.com/careers/search/job/{jid}"))
    return out


def _browser_eval(url, js, wait_selector=None):
    """Load `url` in headless Firefox and return the result of evaluating `js`.

    Shared by the adapters that must run same-origin JS (a bot wall the plain
    client can't pass, or an API needing page-held tokens). Firefox is used
    because it has none of Chromium's headless/automation tells. If Playwright
    is missing, the caller's FetchError degrades the company to a warning.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "needs a browser. Install with: "
            "pip install playwright && playwright install firefox") from exc
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=30000)
            return page.evaluate(js)
        finally:
            browser.close()


_META_JS = r"""async () => {
  const html = document.documentElement.innerHTML;
  const lsd = (html.match(/"LSD",\[\],\{"token":"([^"]+)"/) || [])[1];
  if (!lsd) return { error: "no lsd token" };
  const vars = { search_input: { q: null, divisions: [], offices: [], roles: [],
    leadership_levels: [], saved_jobs: [], saved_searches: [], sub_teams: [],
    teams: [], is_leadership: false, is_remote_only: false, sort_by_new: false,
    results_per_page: null }, viewasUserID: null, isLoggedIn: false };
  const body = new URLSearchParams({ lsd, doc_id: "27129360303422352",
    fb_api_req_friendly_name: "CareersJobSearchResultsV2DataQuery",
    variables: JSON.stringify(vars), __a: "1" });
  const r = await fetch("/graphql", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded",
               "x-fb-lsd": lsd }, body });
  const d = JSON.parse(await r.text());
  return (d.data && d.data.job_search_with_featured_jobs_v2
          && d.data.job_search_with_featured_jobs_v2.all_jobs) || [];
}"""


def fetch_meta(cfg):
    """Meta careers via its GraphQL search, replayed inside the page.

    metacareers.com/graphql needs an `lsd` token minted per page load and a
    same-origin request, so a plain client can't call it. The browser scrapes
    the token and replays CareersJobSearchResultsV2DataQuery, which returns
    every job in one response; locations are plain strings we filter normally.
    """
    jobs = _browser_eval("https://www.metacareers.com/jobs", _META_JS,
                         wait_selector=None)
    if isinstance(jobs, dict) and jobs.get("error"):
        raise FetchError(f"meta: {jobs['error']}")
    if not isinstance(jobs, list):
        raise FetchError("meta: unexpected GraphQL response shape")
    out = []
    for j in jobs:
        jid = j.get("id")
        out.append(_norm(jid, j.get("title"),
                         "; ".join(j.get("locations") or []),
                         f"https://www.metacareers.com/jobs/{jid}/"))
    return out


def fetch_tesla(cfg):
    """Tesla careers via a real browser.

    Akamai fronts www.tesla.com and 403s every plain HTTP client (curl and
    urllib alike, any headers). Playwright-driven Chromium is also flagged --
    headless via the "HeadlessChrome" UA/client-hint brand, and even headed
    via the automation fingerprint. Headless Firefox passes cleanly (no
    client hints, no headless tells), so that's what we drive. If Playwright
    isn't installed the adapter degrades to a FetchError warning and the rest
    of the run proceeds.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Tesla needs a browser to pass Akamai. Install with: "
            "pip install playwright && playwright install firefox") from exc

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            try:
                page = browser.new_page()
                # Don't fetch the state endpoint ourselves -- a hand-made
                # request lacks the Akamai sensor data and gets an HTML
                # challenge back. The careers SPA calls it during load with
                # the sensor attached, so capture that response instead.
                with page.expect_response(
                        lambda r: "cua-api/apps/careers/state" in r.url,
                        timeout=60000) as resp_info:
                    page.goto("https://www.tesla.com/careers/search/",
                              wait_until="domcontentloaded", timeout=60000)
                state = resp_info.value.json()
            finally:
                browser.close()
    except Exception as exc:
        raise FetchError(f"tesla browser fetch failed: {exc}") from exc

    if not isinstance(state, dict) or "listings" not in state:
        raise FetchError("tesla: careers state JSON missing 'listings'")
    return _tesla_jobs_from_state(state)


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
    "optiver": fetch_optiver,
    "jibe": fetch_jibe,
    "tesla": fetch_tesla,
    "radancy": fetch_radancy,
    "uber": fetch_uber,
    "pcsx": fetch_pcsx,
    "phenom": fetch_phenom,
    "meta": fetch_meta,
    "google": fetch_google,
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

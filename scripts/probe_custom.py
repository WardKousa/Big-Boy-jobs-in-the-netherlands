"""One-off probe of candidate custom career endpoints. Prints what works."""
import json
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (label, method, url, body, json_path_hint)
PROBES = [
    ("Google v3alt", "GET",
     "https://careers.google.com/api/v3/search/?page_size=5&location=Netherlands", None),
    ("Google jobs api", "GET",
     "https://www.google.com/about/careers/applications/jobs/results/?location=Netherlands", None),
    ("Uber list", "POST",
     "https://www.uber.com/api/loadSearchJobsResults?localeCode=en",
     {"params": {"location": [{"country": "NLD"}]}, "limit": 5, "page": 0}),
    ("Booking SR", "GET",
     "https://api.smartrecruiters.com/v1/companies/bookingcom/postings?limit=5", None),
    ("Booking careers", "GET",
     "https://jobs.booking.com/api/v2/jobs?limit=5", None),
    ("Atlassian SR", "GET",
     "https://api.smartrecruiters.com/v1/companies/atlassian/postings?limit=5", None),
    ("Atlassian custom", "GET",
     "https://www.atlassian.com/endpoint/careers/listings", None),
    ("Snap careers", "GET",
     "https://careers.snap.com/api/jobs?location=Netherlands", None),
    ("Qualcomm WD External", "POST",
     "https://qualcomm.wd5.myworkdayjobs.com/wday/cxs/qualcomm/External/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("ASML WD wd3 Careers", "POST",
     "https://asml.wd3.myworkdayjobs.com/wday/cxs/asml/ASML_Careers/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("ASML WD External", "POST",
     "https://asml.wd3.myworkdayjobs.com/wday/cxs/asml/External/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("Philips WD", "POST",
     "https://philips.wd3.myworkdayjobs.com/wday/cxs/philips/jobs-and-careers/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("eBay WD wd5 apply", "POST",
     "https://ebay.wd5.myworkdayjobs.com/wday/cxs/ebay/apply/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("Adidas WD wd3", "POST",
     "https://adidas.wd3.myworkdayjobs.com/wday/cxs/adidas/careers/jobs",
     {"limit": 5, "offset": 0, "searchText": ""}),
    ("Meta graphql-lite", "GET",
     "https://www.metacareers.com/jobs?offices[0]=Amsterdam%2C%20Netherlands", None),
    ("ING SR", "GET",
     "https://api.smartrecruiters.com/v1/companies/ing/postings?limit=5", None),
    ("ABNAMRO custom", "GET",
     "https://werkenbij.abnamro.nl/api/vacancies?limit=5", None),
]


def probe(label, method, url, body):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(400)
            ctype = r.headers.get("Content-Type", "")
            print(f"  {label:<26} HTTP {r.status} | {ctype[:30]} | {raw[:120]!r}")
    except urllib.error.HTTPError as e:
        print(f"  {label:<26} HTTP {e.code} (error)")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"  {label:<26} FAIL: {e}")


for p in PROBES:
    probe(*p)

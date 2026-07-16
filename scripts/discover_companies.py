"""
ATS discovery probe.

For each target company, generate candidate slugs from its name and test them
against every supported ATS provider's public JSON endpoint. Whichever endpoint
returns a valid, non-empty job list wins.

Run:  python scripts/discover_companies.py
Output: prints a YAML-ready mapping and writes scripts/discovery_result.json
Stdlib only -- no pip install required.
"""

import concurrent.futures
import json
import re
import urllib.error
import urllib.request

TIMEOUT = 12
USER_AGENT = "Mozilla/5.0 (compatible; job-tracker-discovery/1.0)"

# name -> optional list of explicit slug hints to try first.
# Empty list means "derive candidates from the name".
COMPANIES = {
    "Optiver": ["optiver"],
    "IMC Trading": ["imc", "imctrading", "imcofficial"],
    "Databricks": ["databricks"],
    "Netflix": ["netflix"],
    "Tesla": ["tesla"],
    "ASML": ["asml"],
    "Just Eat Takeaway": ["justeattakeaway", "justeat", "takeaway", "justeattakeawaycom"],
    "Uber": ["uber"],
    "Booking.com": ["booking", "bookingcom"],
    "Meta": ["meta", "facebook"],
    "Google": ["google"],
    "Microsoft": ["microsoft"],
    "Amazon (AWS)": ["amazon", "aws"],
    "Maverick Derivatives": ["maverickderivatives", "maverick"],
    "Flow Traders": ["flowtraders", "flow"],
    "Radix Trading": ["radixtrading", "radix"],
    "Jump Trading": ["jumptrading", "jump"],
    "Philips": ["philips"],
    "Adyen": ["adyen"],
    "Mollie": ["mollie"],
    "Bunq": ["bunq"],
    "Snap": ["snap", "snapchat"],
    "ServiceNow": ["servicenow"],
    "Flexport": ["flexport"],
    "eBay": ["ebay"],
    "Adidas": ["adidas"],
    "SurePrep": ["sureprep"],
    "Webb Traders": ["webbtraders", "webb"],
    "Miro": ["miro", "realtimeboard"],
    "Arcadis": ["arcadis"],
    "TomTom": ["tomtom"],
    "Huawei": ["huawei"],
    "Nvidia": ["nvidia"],
    "Workiva": ["workiva"],
    "Cloudflare": ["cloudflare"],
    "JetBrains": ["jetbrains"],
    "IKEA": ["ikea", "ingka"],
    "KPN": ["kpn"],
    "Thales": ["thales", "thalesgroup"],
    "ABB": ["abb"],
    "Qualcomm": ["qualcomm"],
    "Atlassian": ["atlassian"],
    "Apple": ["apple"],
    "Fivetran": ["fivetran"],
    "Plain": ["plain"],
    "Reddit": ["reddit"],
    "Roblox": ["roblox"],
    "Roku": ["roku"],
    "Spotify": ["spotify"],
    "IBM": ["ibm"],
    "Lemonade": ["lemonade"],
    "Stripe": ["stripe"],
    "Nebius": ["nebius"],
    "Elastic": ["elastic"],
    "Navan": ["navan", "tripactions"],
    "CloudKitchens": ["cloudkitchens", "citystoragesystems"],
    "ClickHouse": ["clickhouse"],
    "Personio": ["personio"],
    "Block (Square)": ["block", "square"],
    "Da Vinci Derivatives": ["davinciderivatives", "davinci"],
    "All Options": ["alloptions"],
    "MessageBird (Bird)": ["messagebird", "bird"],
    "Backbase": ["backbase"],
    "Snowflake": ["snowflake", "snowflakecomputing"],
    "NXP Semiconductors": ["nxp", "nxpsemiconductors"],
    "Shell": ["shell"],
    "ING": ["ing"],
    "ABN AMRO": ["abnamro", "abn"],
    "Rabobank": ["rabobank", "rabo"],
    "Ortec": ["ortec"],
    "GitLab": ["gitlab"],
}

# provider -> (url template, function returning job count from parsed json)
PROVIDERS = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        lambda d: d.get("totalFound", 0) if isinstance(d, dict) else 0,
    ),
    "recruitee": (
        "https://{slug}.recruitee.com/api/offers/",
        lambda d: len(d.get("offers", [])) if isinstance(d, dict) else 0,
    ),
}


def derive_slugs(name, hints):
    base = name.lower()
    base = re.sub(r"\(.*?\)", "", base)          # drop parenthetical
    base = re.sub(r"[^a-z0-9 ]", "", base)        # strip punctuation
    base = base.strip()
    candidates = list(hints)
    candidates.append(base.replace(" ", ""))
    candidates.append(base.replace(" ", "-"))
    first = base.split(" ")[0]
    candidates.append(first)
    seen = set()
    out = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def probe(provider, slug):
    url_tmpl, counter = PROVIDERS[provider]
    url = url_tmpl.format(slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return 0
            data = json.load(r)
            return counter(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError, ConnectionError):
        return 0


def discover_one(name, hints):
    for slug in derive_slugs(name, hints):
        for provider in PROVIDERS:
            count = probe(provider, slug)
            if count > 0:
                return {"name": name, "ats": provider, "slug": slug, "jobs": count}
    return {"name": name, "ats": None, "slug": None, "jobs": 0}


def main():
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures = {
            ex.submit(discover_one, name, hints): name
            for name, hints in COMPANIES.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results[res["name"]] = res
            status = f"{res['ats']}:{res['slug']} ({res['jobs']})" if res["ats"] else "NOT FOUND"
            print(f"  {res['name']:<28} -> {status}")

    with open("scripts/discovery_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    found = [r for r in results.values() if r["ats"]]
    print(f"\nFound {len(found)}/{len(results)} via public ATS endpoints.")
    print("Wrote scripts/discovery_result.json")


if __name__ == "__main__":
    main()

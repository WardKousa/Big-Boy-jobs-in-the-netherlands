"""
Second-pass discovery for companies that use Workday or need alternate slugs.

Workday exposes a JSON endpoint at:
  https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
It's a POST with a JSON body. We brute-force common (wd-number, site) combos
for a curated list of tenant guesses.

Run: python scripts/discover_workday.py
"""

import concurrent.futures
import json
import urllib.error
import urllib.request

TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; job-tracker-discovery/1.0)"

# company -> list of candidate Workday tenant slugs to try
WORKDAY_CANDIDATES = {
    "Netflix": ["netflix"],
    "ASML": ["asml"],
    "Philips": ["philips"],
    "Booking.com": ["booking", "bookingcom", "priceline"],
    "eBay": ["ebay"],
    "Adidas": ["adidas"],
    "NXP Semiconductors": ["nxp"],
    "Shell": ["shell"],
    "TomTom": ["tomtom"],
    "Nvidia": ["nvidia"],
    "Qualcomm": ["qualcomm"],
    "Workiva": ["workiva"],
    "ABN AMRO": ["abnamro"],
    "Rabobank": ["rabobank"],
    "ING": ["ing"],
    "IBM": ["ibm"],
    "ABB": ["abb"],
    "IKEA": ["ikea", "ingka"],
    "Backbase": ["backbase"],
    "Atlassian": ["atlassian"],
    "Arcadis": ["arcadis"],
}

WD_NUMBERS = ["wd1", "wd3", "wd5", "wd2", "wd103"]
SITES = ["External", "Careers", "careers", "External_Careers", "en-US",
         "Global", "Professionals", "Search", "External_Career_Site"]


def probe_workday(tenant, wd, site):
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    body = json.dumps({"limit": 1, "offset": 0, "searchText": ""}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            data = json.load(r)
            total = data.get("total", 0)
            if isinstance(total, int) and total > 0:
                return {"wd": wd, "site": site, "total": total}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError, ConnectionError):
        return None
    return None


def discover_company(name, tenants):
    for tenant in tenants:
        for wd in WD_NUMBERS:
            for site in SITES:
                hit = probe_workday(tenant, wd, site)
                if hit:
                    return {"name": name, "ats": "workday", "tenant": tenant,
                            **hit}
    return {"name": name, "ats": None}


def main():
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(discover_company, n, t): n
                   for n, t in WORKDAY_CANDIDATES.items()}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results[res["name"]] = res
            if res["ats"]:
                print(f"  {res['name']:<22} -> workday tenant={res['tenant']} "
                      f"{res['wd']}/{res['site']} ({res['total']} jobs)")
            else:
                print(f"  {res['name']:<22} -> NOT FOUND")

    with open("scripts/discovery_workday.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    found = sum(1 for r in results.values() if r["ats"])
    print(f"\nFound {found}/{len(results)} on Workday. Wrote scripts/discovery_workday.json")


if __name__ == "__main__":
    main()

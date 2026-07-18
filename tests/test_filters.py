"""Unit tests for the matching, notification, and fetch logic (no network)."""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobtracker import adapters, filters, notify, run, state  # noqa: E402

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


def test_rejects_abbreviated_seniority():
    """eBay writes "Sr." -- which "senior" does not catch."""
    flt = {"include_keywords": ["software engineer"],
           "exclude_keywords": ["senior", "sr"], "locations": []}
    assert filters.matches(
        _job("Sr. Frontend/Fullstack Software Engineer", "Amsterdam"),
        flt) is False
    # ...but must not reject a word merely containing those letters.
    assert filters.matches(
        _job("Software Engineer, SRE Platform", "Amsterdam"), flt) is True


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


def test_weird_internship_title_matches_on_intern():
    flt = {"include_keywords": ["intern"], "locations": ["netherlands"]}
    job = _job("Autopilot Firmware Intern", "Amsterdam, Netherlands")
    assert filters.matches(job, flt) is True


def test_intern_does_not_match_international():
    flt = {"include_keywords": ["intern"], "locations": []}
    job = _job("International Sales Associate", "Amsterdam")
    assert filters.matches(job, flt) is False


def test_lead_exclude_does_not_reject_leading():
    flt = {"include_keywords": ["software engineer"],
           "exclude_keywords": ["lead"], "locations": []}
    job = _job("Software Engineer, Leading Payments Team", "Amsterdam")
    assert filters.matches(job, flt) is True


def test_software_engineer_matches():
    flt = {"include_keywords": ["software engineer"], "locations": ["amsterdam"]}
    job = _job("Software Engineer II", "Amsterdam, NL")
    assert filters.matches(job, flt) is True


def test_sort_by_priority_orders_best_tier_first():
    jobs = [
        {"company": "A", "tier": "A", "title": "t", "location": "l", "url": "u"},
        {"company": "B", "tier": "S++", "title": "t", "location": "l", "url": "u"},
        {"company": "C", "tier": "S", "title": "t", "location": "l", "url": "u"},
        {"company": "D", "tier": "", "title": "t", "location": "l", "url": "u"},
    ]
    ordered = [j["tier"] for j in notify.sort_by_priority(jobs)]
    assert ordered == ["S++", "S", "A", ""]


def test_split_new_only_returns_unseen():
    seen = {}
    jobs = [_job("Data Engineer", "Amsterdam")]
    first = state.split_new(jobs, seen)
    assert len(first) == 1
    second = state.split_new(jobs, seen)  # same job again
    assert len(second) == 0


EARLY = {
    "early_career_keywords": ["intern", "internship", "graduate"],
    "technical_keywords": ["software", "data", "computer science"],
    "locations": [],
}


def test_early_career_needs_technical_signal():
    """'intern' alone must not admit every HR/Law internship a big employer
    posts -- that flood is what buries the real matches."""
    assert filters.matches(
        _job("HR internship: support the Internships Office", "Veldhoven"),
        EARLY) is False
    assert filters.matches(
        _job("Law | Accountancy internship: contract compliance", "Veldhoven"),
        EARLY) is False


def test_early_career_with_technical_signal_matches():
    assert filters.matches(
        _job("Software Engineering Internship: Python tool development", "NL"),
        EARLY) is True
    assert filters.matches(
        _job("Applied Physics Internship: Data Science for Metrology", "NL"),
        EARLY) is True


def test_early_career_term_alone_is_not_enough_without_technical_list():
    """With no technical list configured, behaviour stays legacy include-only."""
    flt = {"include_keywords": ["data engineer"], "locations": []}
    assert filters.matches(_job("Marketing Intern", "Amsterdam"), flt) is False


def test_technical_keywords_do_not_include_ambiguous_pronoun():
    """Regression: 'it' as a keyword matches the pronoun in 'make it happen'."""
    import yaml
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "config", "settings.yaml"),
            encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    tech = cfg["filters"]["technical_keywords"]
    assert "it" not in [t.strip().lower() for t in tech]


def test_intern_whole_word_does_not_match_internship():
    """Both forms must be listed; \\bintern\\b does not cover 'internship'."""
    flt = {"include_keywords": ["intern"], "locations": []}
    assert filters.matches(_job("HR Internship", "Amsterdam"), flt) is False


def _http_error(code, payload):
    """An HTTPError whose body carries Telegram's JSON explanation."""
    body = json.dumps(payload).encode()
    return urllib.error.HTTPError(
        "https://api.telegram.org/botX/sendMessage", code, "Bad Request",
        {}, io.BytesIO(body))


def test_posted_str_handles_every_provider_format():
    """Each ATS reports the posting date differently; all must normalise."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    three_days = now - timedelta(days=3)

    # Lever: epoch milliseconds
    assert adapters._posted_str(three_days.timestamp() * 1000) == "3d ago"
    # Greenhouse / Ashby: ISO with offset
    assert adapters._posted_str(three_days.isoformat()) == "3d ago"
    # SmartRecruiters: ISO with Z
    assert adapters._posted_str(
        three_days.strftime("%Y-%m-%dT%H:%M:%S.000Z")) == "3d ago"
    # Recruitee: "YYYY-MM-DD HH:MM:SS UTC"
    assert adapters._posted_str(
        three_days.strftime("%Y-%m-%d %H:%M:%S UTC")) == "3d ago"
    # Amazon: long-form date, sometimes double-spaced
    assert adapters._posted_str(
        three_days.strftime("%B  %d, %Y").replace(" 0", " ")) == "3d ago"
    # Workday: already prose
    assert adapters._posted_str("Posted Today") == "today"
    assert adapters._posted_str("Posted 30+ Days Ago") == "30+ days ago"


def test_posted_str_is_best_effort_not_fatal():
    for junk in (None, "", "not a date", {}, []):
        assert adapters._posted_str(junk) == ""


def test_alert_line_shows_posting_age_when_known():
    job = {"company": "Philips", "tier": "A", "title": "Data Scientist",
           "location": "Eindhoven", "url": "u", "posted": "12d ago"}
    assert "posted 12d ago" in notify._format_lines([job])[0]
    del job["posted"]
    assert "posted" not in notify._format_lines([job])[0]


def test_dedupe_collapses_same_role_under_different_req_ids():
    """ASML lists one internship under two req ids; alert once."""
    jobs = [
        {"company": "ASML", "source_id": "J-00339923", "title": "CS internship",
         "location": "Veldhoven", "url": "a"},
        {"company": "ASML", "source_id": "J-00339927", "title": "CS internship",
         "location": "Veldhoven", "url": "b"},
    ]
    out = filters.dedupe(jobs)
    assert len(out) == 1
    assert out[0]["source_id"] == "J-00339923"  # keeps the first


def test_dedupe_keeps_distinct_roles_and_locations():
    jobs = [
        {"company": "ASML", "source_id": "1", "title": "CS internship",
         "location": "Veldhoven", "url": "a"},
        {"company": "ASML", "source_id": "2", "title": "SWE internship",
         "location": "Veldhoven", "url": "b"},
        {"company": "ASML", "source_id": "3", "title": "CS internship",
         "location": "Eindhoven", "url": "c"},
    ]
    assert len(filters.dedupe(jobs)) == 3


def test_dedupe_runs_after_state_records_every_source_id():
    """Both twins must land in `seen`, or the dropped one alerts later."""
    seen = {}
    jobs = [
        {"company": "ASML", "source_id": "A", "title": "CS internship",
         "location": "Veldhoven", "url": "a"},
        {"company": "ASML", "source_id": "B", "title": "CS internship",
         "location": "Veldhoven", "url": "b"},
    ]
    new = filters.dedupe(state.split_new(jobs, seen))
    assert len(new) == 1
    assert "ASML::A" in seen and "ASML::B" in seen
    assert filters.dedupe(state.split_new(jobs, seen)) == []


VALID_TOKEN = "123456789:AAHk9v-Wq3nP_xZyL0mB7dR4tS6uV8wX2yA"


def test_valid_token_shape_accepted():
    assert notify._token_complaint(VALID_TOKEN) is None


def test_malformed_tokens_are_explained_not_echoed():
    """404 from Telegram means a malformed token. Catch the mangled-paste
    shapes locally, and never echo the secret into a public CI log."""
    cases = {
        "bot123456789:AAHk9v-Wq3nP_xZyL0mB7dR4tS6uV8wX2yA": "bot",
        "https://api.telegram.org/bot123456789:AAHk": "URL",
        # The real CI failure: 35 chars, no colon -- the bot id prefix was
        # dropped when pasting into the GitHub secret.
        "AAHk9v-Wq3nP_xZyL0mB7dR4tS6uV8wX2yA": "half AFTER the colon",
        "short-no-colon": "no colon",
        "abcdefgh:AAHk9v-Wq3nP_xZyL0mB7dR4tS6uV8wX2yA": "numeric bot id",
        "123456789:AAHk": "truncated",
    }
    for token, expected in cases.items():
        complaint = notify._token_complaint(token)
        assert complaint is not None, f"should reject {token[:12]}"
        assert expected in complaint, f"{expected!r} not in {complaint!r}"
        assert token not in complaint, "must not echo the secret"


def test_preflight_rejects_malformed_token_before_any_network_call():
    os.environ["TELEGRAM_BOT_TOKEN"] = "botAAHk9v-Wq3nP"
    os.environ["TELEGRAM_CHAT_ID"] = "123"
    try:
        notify.preflight({"notifications": {"channel": "telegram"}})
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "malformed" in str(exc)
        assert "BotFather" in str(exc)
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_preflight_ignores_console_channel():
    notify.preflight({"notifications": {"channel": "console"}})


def test_telegram_404_hint_says_malformed_not_missing():
    """The real CI failure: 404 with an unhelpful 'bot does not exist'."""
    def fake_urlopen(req, timeout=None):
        raise _http_error(404, {"error_code": 404, "description": "Not Found"})

    orig = notify.urllib.request.urlopen
    notify.urllib.request.urlopen = fake_urlopen
    try:
        notify._send_telegram_text(VALID_TOKEN, "1", "hi", sleep=lambda s: None)
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "malformed" in str(exc)
        assert "BotFather" in str(exc)
    finally:
        notify.urllib.request.urlopen = orig


def test_telegram_error_surfaces_description_not_bare_status():
    """The regression behind the CI failure: 'HTTP Error 400: Bad Request'
    alone is unactionable; the body names the real problem."""
    exc = _http_error(400, {"ok": False, "error_code": 400,
                            "description": "Bad Request: chat not found"})
    detail = notify._http_detail(exc)
    assert "chat not found" in detail


def test_telegram_400_raises_notify_error_with_hint():
    def fake_urlopen(req, timeout=None):
        raise _http_error(400, {"description": "Bad Request: chat not found"})

    orig = notify.urllib.request.urlopen
    notify.urllib.request.urlopen = fake_urlopen
    try:
        notify._send_telegram_text("tok", "bad", "hi", sleep=lambda s: None)
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "chat not found" in str(exc)
        assert "TELEGRAM_CHAT_ID" in str(exc)
    finally:
        notify.urllib.request.urlopen = orig


def test_telegram_retries_rate_limit_then_succeeds():
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, {"parameters": {"retry_after": 1}})

        class R:
            def __enter__(self): return io.BytesIO(b'{"ok":true}')
            def __exit__(self, *a): return False
        return R()

    orig = notify.urllib.request.urlopen
    notify.urllib.request.urlopen = fake_urlopen
    try:
        notify._send_telegram_text("tok", "1", "hi", sleep=lambda s: None)
        assert calls["n"] == 2
    finally:
        notify.urllib.request.urlopen = orig


def test_telegram_sends_every_batch_to_every_recipient():
    """Comma-separated TELEGRAM_CHAT_ID fans out to each chat -- pressing
    Start on a bot subscribes nobody; every recipient must be listed."""
    os.environ["TELEGRAM_BOT_TOKEN"] = VALID_TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = " 111 , 222 "
    sent = []
    orig = notify._send_telegram_text
    notify._send_telegram_text = (
        lambda token, chat_id, text, sleep=None: sent.append(chat_id))
    try:
        notify.notify_telegram([_job("Data Engineer", "Amsterdam")], {})
        assert sent == ["111", "222"]
    finally:
        notify._send_telegram_text = orig
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_telegram_single_recipient_still_works():
    os.environ["TELEGRAM_BOT_TOKEN"] = VALID_TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = "12345"
    sent = []
    orig = notify._send_telegram_text
    notify._send_telegram_text = (
        lambda token, chat_id, text, sleep=None: sent.append(chat_id))
    try:
        notify.notify_telegram([_job("Data Engineer", "Amsterdam")], {})
        assert sent == ["12345"]
    finally:
        notify._send_telegram_text = orig
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_telegram_chat_id_of_only_commas_counts_as_unset():
    os.environ["TELEGRAM_BOT_TOKEN"] = VALID_TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = " , ,, "
    try:
        notify.validate_telegram_env()
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "not set" in str(exc)
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_telegram_failure_names_recipient_position_not_value():
    """A bad second id must be attributable without echoing anyone's chat id
    into a public CI log."""
    os.environ["TELEGRAM_BOT_TOKEN"] = VALID_TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = "111,222"

    def fail_on_second(token, chat_id, text, sleep=None):
        if chat_id == "222":
            raise notify.NotifyError("Telegram rejected the message: "
                                     "HTTP 400: chat not found")

    orig = notify._send_telegram_text
    notify._send_telegram_text = fail_on_second
    try:
        notify.notify_telegram([_job("Data Engineer", "Amsterdam")], {})
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "recipient #2 of 2" in str(exc)
        assert "222" not in str(exc).replace("#2 of 2", "")
    finally:
        notify._send_telegram_text = orig
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_telegram_missing_secrets_raises_notify_error():
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ.pop(key, None)
    try:
        notify.notify_telegram([_job("Data Engineer", "Amsterdam")], {})
        raise AssertionError("expected NotifyError")
    except notify.NotifyError as exc:
        assert "TELEGRAM_BOT_TOKEN" in str(exc)


def test_telegram_batches_stay_under_api_limit():
    jobs = [{"company": f"C{i}", "tier": "S", "title": "Data Engineer " + "x" * 90,
             "location": "Amsterdam, Netherlands",
             "url": "https://example.com/" + "y" * 120} for i in range(60)]
    texts = notify.telegram_batches(notify._format_lines(jobs), len(jobs))
    assert texts, "expected at least one batch"
    for t in texts:
        assert len(t) <= 4096, f"batch of {len(t)} chars exceeds Telegram cap"
        assert t.strip(), "empty batch would be rejected by Telegram"


def test_telegram_batches_never_emits_empty_batch_for_long_line():
    huge = "x" * 5000
    texts = notify.telegram_batches([huge], 1)
    assert all(t.strip() for t in texts)
    assert len(texts) == 1


def test_fetch_all_contains_unexpected_adapter_exception():
    """One provider changing its JSON shape must not kill the whole run."""
    orig = adapters.fetch_company

    def boom(cfg):
        if cfg["name"] == "Bad":
            raise KeyError("locationsText")
        return [{"company": cfg["name"], "source_id": "1", "title": "t",
                 "location": "l", "url": "u"}]

    adapters.fetch_company = boom
    try:
        jobs, errors = run.fetch_all([{"name": "Bad", "ats": "workday"},
                                      {"name": "Good", "ats": "greenhouse"}])
        assert len(jobs) == 1, "healthy company should still be fetched"
        assert any("KeyError" in e for e in errors)
    finally:
        adapters.fetch_company = orig


def test_pcsx_maps_positions_and_paginates():
    """Microsoft's Eightfold pcsx API: data nested under 'data', page fixed
    at whatever the server returns, postedTs is epoch seconds."""
    def fake_get_json(url, data=None, method="GET", headers=None):
        start = int(url.split("start=")[1].split("&")[0])
        remaining = 24 - start
        n = max(0, min(10, remaining))
        return {"data": {"count": 24, "positions": [
            {"id": 100 + start + i, "name": "Data Scientist",
             "locations": ["Netherlands, Amsterdam"],
             "positionUrl": f"/careers/job/{100 + start + i}",
             "postedTs": 1783942116}
            for i in range(n)]}}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_pcsx({"host": "apply.careers.microsoft.com",
                                   "domain": "microsoft.com",
                                   "location": "Netherlands"})
        assert len(out) == 24
        assert out[0]["url"].startswith("https://apply.careers.microsoft.com/")
        assert out[0]["posted"]  # epoch parsed
    finally:
        adapters._get_json = orig


def test_uber_maps_results_and_flattens_locations():
    def fake_get_json(url, data=None, method="GET", headers=None):
        assert headers and headers.get("x-csrf-token") == "x"
        page = data["params"]["page"]
        if page > 0:
            return {"data": {"results": [], "totalResults": {"low": 2}}}
        return {"data": {"totalResults": {"low": 2}, "results": [
            {"id": 1, "title": "Software Engineer",
             "allLocations": [{"city": "Amsterdam", "countryName": "Netherlands"}],
             "creationDate": "2026-06-19T15:11:33.000Z"},
            {"id": 2, "title": "Data Scientist",
             "allLocations": [{"city": "Amsterdam", "countryName": "Netherlands"}],
             "creationDate": "2026-06-19T15:11:33.000Z"}]}}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_uber({})
        assert len(out) == 2
        assert out[0]["location"] == "Amsterdam, Netherlands"
        assert out[0]["url"].endswith("/careers/list/1/")
    finally:
        adapters._get_json = orig


def test_phenom_reads_refine_search_and_builds_job_url():
    def fake_get_json(url, data=None, method="GET", headers=None):
        assert url.endswith("/widgets")
        offset = data["from"]
        if offset > 0:
            return {"refineSearch": {"totalHits": 1, "data": {"jobs": []}}}
        return {"refineSearch": {"totalHits": 1, "data": {"jobs": [
            {"jobId": "R_050311", "title": "Product Owner",
             "city": "Amsterdam", "country": "Netherlands",
             "postedDate": "2026-06-10T00:00:00.000+0000"}]}}}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_phenom({"host": "careers.justeattakeaway.com",
                                     "lang": "en_global", "lang_path": "en"})
        assert len(out) == 1
        assert out[0]["location"] == "Amsterdam, Netherlands"
        assert out[0]["url"] == ("https://careers.justeattakeaway.com/"
                                 "global/en/job/R_050311")
        assert out[0]["posted"]
    finally:
        adapters._get_json = orig


def test_google_extracts_id_and_title_from_result_slug():
    page_html = (
        'x <a href="/about/careers/applications/jobs/results/'
        '105742843280007878-ai-sales-specialist-iii-google-cloud">y</a> '
        'z jobs/results/108077358694441670-electrical-technician q')

    def fake_get_text(url):
        return page_html if "page=1" in url else ""

    orig = adapters._get_text
    adapters._get_text = fake_get_text
    try:
        out = adapters.fetch_google({"query": "Netherlands",
                                     "location_label": "Netherlands"})
        assert len(out) == 2
        assert out[0]["source_id"] == "105742843280007878"
        assert out[0]["title"] == "Ai Sales Specialist Iii Google Cloud"
        assert out[0]["location"] == "Netherlands"
        assert "105742843280007878-ai-sales" in out[0]["url"]
    finally:
        adapters._get_text = orig


RADANCY_ITEM_HTML = """
<li class="search-results-item vacancy-item">
  <a href="/en/job/amsterdam/ai-finance-transformation-intern/3121/999" data-job-id="999">
    <h2 class="vacancy-item__title">AI &amp; Finance Transformation Intern</h2>
  </a>
  <ul><li><span class="job-location">Amsterdam, Netherlands</span></li></ul>
  <a href="/en/job/amsterdam/ai-finance-transformation-intern/3121/999" data-job-id="999" class="btn btn-icon">
    <span class="sr-only">Show job</span>
  </a>
</li>
"""


def test_radancy_parses_items_and_unescapes_entities():
    """ING titles arrive HTML-escaped ("AI &amp; Finance..."); the alert must
    show the real text, and the btn-icon anchor (same data-job-id, no h2)
    must not produce a duplicate."""
    def fake_get_json(url, data=None, method="GET"):
        return {"results": RADANCY_ITEM_HTML}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_radancy({"host": "careers.ing.com"})
        assert len(out) == 1
        assert out[0]["title"] == "AI & Finance Transformation Intern"
        assert out[0]["location"] == "Amsterdam, Netherlands"
        assert out[0]["source_id"] == "999"
        assert out[0]["url"].startswith("https://careers.ing.com/en/job/")
    finally:
        adapters._get_json = orig


def test_radancy_stops_on_short_page():
    calls = {"n": 0}

    def fake_get_json(url, data=None, method="GET"):
        calls["n"] += 1
        return {"results": RADANCY_ITEM_HTML}  # 1 item < page size

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        adapters.fetch_radancy({"host": "careers.ing.com"})
        assert calls["n"] == 1, "a short page must end pagination"
    finally:
        adapters._get_json = orig


def test_workday_paginates_when_total_only_on_first_page():
    """Nvidia/Philips/NXP/eBay report `total` on page 1 and 0 afterwards.
    Re-reading it each page capped them at 40 jobs out of hundreds."""
    pages = []

    def fake_get_json(url, data=None, method="GET"):
        offset = data["offset"]
        remaining = 95 - offset
        n = max(0, min(20, remaining))
        pages.append(offset)
        return {
            # total only on the first page; 0 thereafter, like the real API
            "total": 95 if offset == 0 else 0,
            "jobPostings": [{"externalPath": f"/job/{offset + i}",
                             "title": "Data Engineer", "locationsText": "NL",
                             "bulletFields": [f"R-{offset + i}"]}
                            for i in range(n)],
        }

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_workday({"tenant": "t", "wd": "wd3", "site": "S"})
        assert len(out) == 95, f"expected all 95 jobs, got {len(out)}"
        assert pages[:3] == [0, 20, 40], "should keep paging past offset 40"
    finally:
        adapters._get_json = orig


def test_workday_stops_when_a_page_is_empty():
    """No `total` at all must still terminate, not spin to max_pages."""
    calls = {"n": 0}

    def fake_get_json(url, data=None, method="GET"):
        calls["n"] += 1
        if data["offset"] == 0:
            return {"jobPostings": [{"externalPath": "/job/1", "title": "t",
                                     "locationsText": "NL"}]}
        return {"jobPostings": []}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_workday({"tenant": "t", "wd": "wd3", "site": "S"})
        assert len(out) == 1
        assert calls["n"] == 2, "should stop on the first empty page"
    finally:
        adapters._get_json = orig


def test_tesla_state_transform_labels_nl_and_keeps_lookup_for_rest():
    """NL location ids (from the geo tree) must render as "City, Netherlands"
    -- Tesla's lookup strings only name the province, which the location
    filter would miss for cities like Tilburg."""
    state = {
        "geo": [{"id": "3", "sites": [{"id": "NL", "cities": {
            "Tilburg": ["321"], "Amsterdam": ["831"]}}]}],
        "lookup": {"locations": {"321": "Tilburg, Noord-brabant",
                                 "831": "Amsterdam, Zuid-holland",
                                 "100": "Austin, Texas"}},
        "listings": [
            {"id": "1", "t": "Service Technician", "l": "321"},
            {"id": "2", "t": "Software Engineer", "l": "831"},
            {"id": "3", "t": "AI Engineer", "l": "100"},
        ],
    }
    out = adapters._tesla_jobs_from_state(state)
    by_id = {j["source_id"]: j for j in out}
    assert by_id["1"]["location"] == "Tilburg, Netherlands"
    assert by_id["2"]["location"] == "Amsterdam, Netherlands"
    assert by_id["3"]["location"] == "Austin, Texas"
    assert by_id["2"]["url"] == "https://www.tesla.com/careers/search/job/2"


def test_jibe_paginates_until_total_count():
    def fake_get_json(url, data=None, method="GET"):
        page = int(url.split("page=")[1].split("&")[0])
        n = 100 if page == 1 else 20
        return {"totalCount": 120, "jobs": [
            {"data": {"req_id": f"p{page}-{i}", "slug": f"p{page}-{i}",
                      "title": "Data Engineer", "city": "Amsterdam",
                      "country": "Netherlands",
                      "full_location": "Amsterdam, Netherlands",
                      "posted_date": "2026-07-01T00:00:00+0000"}}
            for i in range(n)]}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_jibe({"host": "jobs.example.com",
                                   "board": "acme", "location": "Netherlands"})
        assert len(out) == 120
        assert out[0]["url"] == "https://jobs.example.com/acme/jobs/p1-0"
        assert out[0]["location"] == "Amsterdam, Netherlands"
        assert out[0]["posted"]  # parsed, not empty
    finally:
        adapters._get_json = orig


def test_optiver_pages_with_from_size_and_builds_absolute_urls():
    def fake_get_json(url, data=None, method="GET"):
        start = int(url.split("from=")[1].split("&")[0])
        remaining = max(0, 40 - start)
        n = min(16, remaining)
        return {"totalCount": 40, "items": [
            {"title": f"Job {start + i}", "location": "Amsterdam",
             "href": f"/join-us/jobs/technology/amsterdam/job-{start + i}/"}
            for i in range(n)]}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_optiver({})
        assert len(out) == 40
        assert out[0]["url"].startswith("https://www.optiver.com/join-us/")
        assert out[0]["source_id"].startswith("/join-us/")
    finally:
        adapters._get_json = orig


def test_workday_empty_bulletfields_does_not_crash():
    posting = {"externalPath": "/job/X", "locationsText": "Amsterdam",
               "title": "Data Engineer", "bulletFields": []}
    captured = {}

    def fake_get_json(url, data=None, method="GET"):
        captured["hit"] = True
        return {"jobPostings": [posting], "total": 1}

    orig = adapters._get_json
    adapters._get_json = fake_get_json
    try:
        out = adapters.fetch_workday(
            {"tenant": "t", "wd": "wd3", "site": "S", "max_pages": 1})
        assert len(out) == 1
        assert out[0]["source_id"] == "/job/X"  # falls back to externalPath
    finally:
        adapters._get_json = orig


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

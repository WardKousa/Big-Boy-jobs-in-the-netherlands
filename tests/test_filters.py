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
        "AAHk9v-Wq3nP_xZyL0mB7dR4tS6uV8wX2yA": "colon",
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

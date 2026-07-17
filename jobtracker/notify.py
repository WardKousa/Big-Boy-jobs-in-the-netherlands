"""
Notifiers. Console works with no setup. Telegram and email read all secrets
from environment variables -- never hardcode tokens in config.
"""

import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
from email.mime.text import MIMEText

MAX_ITEMS_PER_MESSAGE = 30

# Telegram hard-caps messages at 4096 UTF-16 code units. Budget below that:
# the header and the "\n\n" separators are added on top of the line lengths.
TELEGRAM_TEXT_LIMIT = 3500
TELEGRAM_SEND_RETRIES = 4
TELEGRAM_TIMEOUT = 20

# A bot token is "<bot_id>:<secret>", e.g. 123456789:AAHk9v-Wq...
TELEGRAM_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

TOKEN_HELP = ("Get a fresh one from @BotFather (/mybots -> your bot -> API "
              "Token) and update the TELEGRAM_BOT_TOKEN secret under "
              "Settings -> Secrets and variables -> Actions.")

# Operator hints for the Telegram errors that are actually worth acting on.
# Telegram's own wording is accurate but doesn't say which secret to fix.
TELEGRAM_HINTS = {
    400: ("Check TELEGRAM_CHAT_ID. It must be the numeric id from "
          "scripts/telegram_setup.py (negative for groups), not a @username."),
    401: ("TELEGRAM_BOT_TOKEN is well-formed but Telegram does not recognise "
          "it -- most likely revoked or from a deleted bot. " + TOKEN_HELP),
    403: ("The bot cannot message this chat. Open the bot in Telegram and "
          "press Start, then retry."),
    # Verified against the live API: a well-formed but invalid token returns
    # 401, while 404 means the /bot<token>/ path itself does not resolve. So a
    # 404 is always a mangled value, never merely a wrong one.
    404: ("TELEGRAM_BOT_TOKEN is malformed -- the value is mangled, not just "
          "wrong (a wrong-but-valid-looking token would return 401).\n"
          "  Common causes: pasting the whole 'TELEGRAM_BOT_TOKEN = 123:ABC' "
          "line instead of just '123:ABC'; a leading 'bot'; quotes; or a "
          "truncated copy.\n  " + TOKEN_HELP),
}


class NotifyError(Exception):
    """Raised when a notification channel cannot deliver.

    Carries the provider's own explanation, not just the HTTP status: a bare
    "HTTP Error 400: Bad Request" is unactionable, whereas Telegram's body
    says exactly which secret is wrong.
    """


# Priority order for sorting alerts (best first). Unknown tiers sort last.
TIER_ORDER = {"S++": 0, "S+": 1, "S": 2, "A+": 3, "A": 4, "B": 5, "C": 6}


def _tier_rank(job):
    return TIER_ORDER.get(job.get("tier", ""), 99)


def sort_by_priority(jobs):
    """Best-tier first, then alphabetical by company."""
    return sorted(jobs, key=lambda j: (_tier_rank(j), j.get("company", "")))


def _format_lines(new_jobs):
    lines = []
    for j in sort_by_priority(new_jobs):
        loc = f" — {j['location']}" if j.get("location") else ""
        tier = f"{j['tier']} · " if j.get("tier") else ""
        lines.append(f"• [{tier}{j['company']}] {j['title']}{loc}\n  {j['url']}")
    return lines


def notify_console(new_jobs, _settings):
    if not new_jobs:
        print("No new matching jobs.")
        return
    print(f"\n{len(new_jobs)} new matching job(s):\n")
    print("\n".join(_format_lines(new_jobs)))


def _token_complaint(token):
    """Why this string cannot be a bot token, or None if it looks like one.

    Never echoes the token: CI logs are public, and GitHub only masks the exact
    secret value, not a mangled variant of it. Reports shape only.
    """
    if TELEGRAM_TOKEN_RE.match(token):
        return None
    if token.startswith("bot"):
        return ('it starts with "bot" -- paste only the token itself, the '
                'code adds the "bot" prefix to the URL')
    if token.startswith(("http://", "https://")):
        return "it looks like a URL -- paste only the token, not the API URL"
    if ":" not in token:
        return (f"it has no colon (got {len(token)} chars); a token looks like "
                "123456789:AAHk9v-Wq...")
    bot_id, _, secret = token.partition(":")
    if not bot_id.isdigit():
        return "the part before the colon must be the numeric bot id"
    if len(secret) < 30:
        return (f"the part after the colon is only {len(secret)} chars -- it "
                "looks truncated")
    return f"it does not match the expected format (got {len(token)} chars)"


def validate_telegram_env():
    """Check the Telegram secrets before any network call.

    A malformed token otherwise costs a full 6700-posting fetch before failing,
    and does so with Telegram's opaque 404.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise NotifyError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. Run "
            "scripts/telegram_setup.py and add both as GitHub Actions secrets.")
    complaint = _token_complaint(token)
    if complaint:
        raise NotifyError(f"TELEGRAM_BOT_TOKEN is malformed: {complaint}.\n"
                          f"  {TOKEN_HELP}")
    return token, chat_id


def preflight(settings):
    """Validate the configured channel's credentials up front.

    Config errors should surface in a second, not after a full fetch.
    """
    channel = (settings.get("notifications") or {}).get("channel", "console")
    if channel == "telegram":
        validate_telegram_env()


def _http_detail(exc):
    """Extract Telegram's own error text from an HTTPError.

    urllib's str(HTTPError) is only "HTTP Error 400: Bad Request"; the response
    body holds the actual reason ("chat not found"). Reading it is what makes a
    failed send diagnosable from a CI log.
    """
    try:
        body = exc.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return f"HTTP {exc.code}"
    try:
        desc = json.loads(body).get("description")
    except ValueError:
        desc = None
    return f"HTTP {exc.code}: {desc or body[:200]}"


def _retry_after(exc, default):
    """Seconds Telegram asks us to wait after a 429, if it says."""
    try:
        params = json.loads(exc.read().decode("utf-8", "replace"))
        return int(params.get("parameters", {}).get("retry_after", default))
    except (OSError, ValueError, TypeError):
        return default


def telegram_batches(lines, total):
    """Split formatted lines into message-sized chunks.

    Sizing accounts for the header and the "\\n\\n" separators, not just the raw
    line lengths, so a full batch cannot overshoot Telegram's cap.
    """
    header = f"🚨 {total} new job(s):\n\n"
    batches, chunk, size = [], [], len(header)
    for line in lines:
        # `chunk and` keeps an over-long first line from emitting an empty batch.
        if chunk and (size + len(line) + 2 > TELEGRAM_TEXT_LIMIT
                      or len(chunk) >= MAX_ITEMS_PER_MESSAGE):
            batches.append(chunk)
            chunk, size = [], len(header)
        chunk.append(line)
        size += len(line) + 2
    if chunk:
        batches.append(chunk)
    return [header + "\n\n".join(b) for b in batches]


def _send_telegram_text(token, chat_id, text, sleep=time.sleep):
    """POST one message, retrying rate limits and transient network errors."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id, "text": text,
        "disable_web_page_preview": True,
    }).encode()

    last = "unknown error"
    for attempt in range(TELEGRAM_SEND_RETRIES):
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code == 429:
                sleep(_retry_after(exc, 2 ** attempt))
                last = "HTTP 429: rate limited"
                continue
            if code >= 500:
                last = _http_detail(exc)
                sleep(2 ** attempt)
                continue
            # Other 4xx are configuration errors; retrying cannot help.
            detail = _http_detail(exc)
            hint = TELEGRAM_HINTS.get(code)
            raise NotifyError(
                f"Telegram rejected the message: {detail}"
                + (f"\n  Hint: {hint}" if hint else "")) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                ValueError) as exc:
            last = str(exc)
            sleep(2 ** attempt)

    raise NotifyError(
        f"Telegram send failed after {TELEGRAM_SEND_RETRIES} attempts: {last}")


def notify_telegram(new_jobs, _settings, sleep=time.sleep):
    # Secrets arrive via CI and often carry stray whitespace when pasted.
    token, chat_id = validate_telegram_env()
    if not new_jobs:
        return
    for text in telegram_batches(_format_lines(new_jobs), len(new_jobs)):
        _send_telegram_text(token, chat_id, text, sleep=sleep)


def notify_email(new_jobs, _settings):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to_addr = os.environ.get("EMAIL_TO", user)
    if not all([host, user, password, to_addr]):
        raise NotifyError("SMTP_HOST/SMTP_USER/SMTP_PASS/EMAIL_TO not set")
    if not new_jobs:
        return
    body = "\n\n".join(_format_lines(new_jobs))
    msg = MIMEText(body)
    msg["Subject"] = f"{len(new_jobs)} new job(s) matching your filters"
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"Email send failed: {exc}") from exc


NOTIFIERS = {
    "console": notify_console,
    "telegram": notify_telegram,
    "email": notify_email,
}


def notify(new_jobs, settings):
    channel = (settings.get("notifications") or {}).get("channel", "console")
    fn = NOTIFIERS.get(channel, notify_console)
    fn(new_jobs, settings)

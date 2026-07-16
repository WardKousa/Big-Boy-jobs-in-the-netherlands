"""
Notifiers. Console works with no setup. Telegram and email read all secrets
from environment variables -- never hardcode tokens in config.
"""

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.mime.text import MIMEText

MAX_ITEMS_PER_MESSAGE = 30


def _format_lines(new_jobs):
    lines = []
    for j in new_jobs:
        loc = f" — {j['location']}" if j.get("location") else ""
        lines.append(f"• [{j['company']}] {j['title']}{loc}\n  {j['url']}")
    return lines


def notify_console(new_jobs, _settings):
    if not new_jobs:
        print("No new matching jobs.")
        return
    print(f"\n{len(new_jobs)} new matching job(s):\n")
    print("\n".join(_format_lines(new_jobs)))


def notify_telegram(new_jobs, _settings):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    if not new_jobs:
        return
    lines = _format_lines(new_jobs)
    # Telegram caps messages at 4096 chars; chunk conservatively.
    chunk, size = [], 0
    batches = []
    for line in lines:
        if size + len(line) > 3500 or len(chunk) >= MAX_ITEMS_PER_MESSAGE:
            batches.append(chunk)
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        batches.append(chunk)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for batch in batches:
        text = f"🚨 {len(new_jobs)} new job(s):\n\n" + "\n\n".join(batch)
        payload = json.dumps({
            "chat_id": chat_id, "text": text,
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Telegram send failed: {exc}") from exc


def notify_email(new_jobs, _settings):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    to_addr = os.environ.get("EMAIL_TO", user)
    if not all([host, user, password, to_addr]):
        raise RuntimeError("SMTP_HOST/SMTP_USER/SMTP_PASS/EMAIL_TO not set")
    if not new_jobs:
        return
    body = "\n\n".join(_format_lines(new_jobs))
    msg = MIMEText(body)
    msg["Subject"] = f"{len(new_jobs)} new job(s) matching your filters"
    msg["From"] = user
    msg["To"] = to_addr
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())


NOTIFIERS = {
    "console": notify_console,
    "telegram": notify_telegram,
    "email": notify_email,
}


def notify(new_jobs, settings):
    channel = (settings.get("notifications") or {}).get("channel", "console")
    fn = NOTIFIERS.get(channel, notify_console)
    fn(new_jobs, settings)

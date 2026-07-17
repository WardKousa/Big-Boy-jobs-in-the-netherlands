"""
Telegram setup helper.

1. Create a bot: message @BotFather -> /newbot -> copy the token it gives you.
2. Everyone who should get alerts opens the bot in Telegram and taps Start
   (or sends it any message) -- bots cannot start conversations themselves.
3. Run this, pasting your token when asked. It lists every chat id that has
   messaged the bot and sends each a test message so you know it works.

    python scripts/telegram_setup.py

Nothing is stored -- copy the printed chat id into your GitHub secrets.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def call(token, method):
    url = API.format(token=token, method=method)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def main():
    # Token can come from an argument, the env var, or an interactive prompt
    # -- the prompt only works in a real terminal (input() EOFs elsewhere).
    token = ""
    if len(sys.argv) > 1:
        token = sys.argv[1].strip()
    if not token:
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token and sys.stdin.isatty():
        token = input("Paste your bot token (from BotFather): ").strip()
    if not token:
        print("No token given. Run one of:")
        print("  python scripts/telegram_setup.py <BOT_TOKEN>")
        print("  set TELEGRAM_BOT_TOKEN first, then run without arguments")
        return

    data = call(token, "getUpdates")
    if not data.get("ok"):
        print("Telegram rejected the token. Double-check it.")
        print(data)
        return

    results = data.get("result", [])
    # Every distinct chat that has messaged the bot -- yours, a friend's, a
    # group's. Each person who wants alerts must message the bot first (bots
    # cannot start conversations).
    chats = {}
    for upd in results:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            chats[chat["id"]] = (chat.get("username")
                                 or chat.get("title")
                                 or chat.get("first_name", ""))

    if not chats:
        print("\nNo messages found yet. Everyone who wants alerts should open")
        print("the bot in Telegram and tap Start (or send it any message),")
        print("then run this script again. Note: getUpdates only shows recent")
        print("messages, so ask them to send a fresh one if theirs is old.")
        return

    print(f"\nFound {len(chats)} chat(s) that have messaged the bot:")
    for cid, who in chats.items():
        print(f"  {cid}  ({who or 'unknown'})")

    # Send a test message to each so everyone can confirm on their phone.
    for cid, who in chats.items():
        payload = json.dumps({
            "chat_id": cid,
            "text": "✅ Job tracker connected. You'll get alerts here.",
        }).encode()
        req = urllib.request.Request(
            API.format(token=token, method="sendMessage"),
            data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                ok = json.load(r).get("ok")
        except urllib.error.URLError:
            ok = False
        print(f"Test message to {who or cid}: {'sent' if ok else 'FAILED'}")

    chat_id = ",".join(str(c) for c in chats)

    # Print each value alone on its line. A "NAME = value" layout invites
    # copying the whole line into the secret, which yields a mangled token and
    # an opaque HTTP 404 from Telegram at the worst possible moment.
    print("\n--- Add these as GitHub Actions secrets ---")
    print("Settings -> Secrets and variables -> Actions -> New repository secret")
    print("Copy ONLY the value line -- no name, no quotes, no spaces.\n")
    print("Name:  TELEGRAM_BOT_TOKEN")
    print("Value:")
    print(token)
    print("\nName:  TELEGRAM_CHAT_ID")
    print("Value:  (comma-separated; drop any id that shouldn't get alerts)")
    print(chat_id)
    print("\nThen verify without waiting for a real posting:")
    print("  python -m jobtracker.run --test-notify")


if __name__ == "__main__":
    main()

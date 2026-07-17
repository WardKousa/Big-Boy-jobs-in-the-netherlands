"""
Telegram setup helper.

1. Create a bot: message @BotFather -> /newbot -> copy the token it gives you.
2. Open your new bot in Telegram and tap Start (or send it any message).
3. Run this, pasting your token when asked. It finds your chat id and sends a
   test message so you know it works.

    python scripts/telegram_setup.py

Nothing is stored -- copy the printed chat id into your GitHub secrets.
"""

import json
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def call(token, method):
    url = API.format(token=token, method=method)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def main():
    token = input("Paste your bot token (from BotFather): ").strip()
    if not token:
        print("No token given. Aborting.")
        return

    data = call(token, "getUpdates")
    if not data.get("ok"):
        print("Telegram rejected the token. Double-check it.")
        print(data)
        return

    results = data.get("result", [])
    chat_ids = []
    for upd in results:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            chat_ids.append((chat["id"],
                             chat.get("username") or chat.get("first_name", "")))

    if not chat_ids:
        print("\nNo messages found yet. Open your bot in Telegram, tap Start")
        print("(or send it any message), then run this script again.")
        return

    chat_id, who = chat_ids[-1]
    print(f"\nFound your chat id: {chat_id}  (for '{who}')")

    # Send a test message.
    payload = json.dumps({
        "chat_id": chat_id,
        "text": "✅ Job tracker connected. You'll get alerts here.",
    }).encode()
    req = urllib.request.Request(
        API.format(token=token, method="sendMessage"),
        data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        ok = json.load(r).get("ok")
    print("Test message sent — check Telegram!" if ok else "Test send failed.")

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
    print("Value:")
    print(chat_id)
    print("\nThen verify without waiting for a real posting:")
    print("  python -m jobtracker.run --test-notify")


if __name__ == "__main__":
    main()

"""Phone notes via ntfy. The topic comes from the NTFY_TOPIC secret (never committed).

A note is never worth a lost trade: sending retries a few times and then only warns, so an ntfy hiccup can't fail
a desk run before its state is saved."""
import json
import os
import time

import requests

LIMIT = 3700  # ntfy turns messages over ~4 KB into attachments


def fit(head, sections, footer=""):
    """Most important first; whole sections are added only while they fit."""
    out, skipped = list(head), []
    for title, lines in sections:
        if not lines:
            continue
        block = ["", title] + list(lines)
        if len("\n".join(out + block).encode()) < LIMIT:
            out += block
        else:
            skipped.append(title.split(" (")[0].title())
    if skipped:
        out += ["", "More on the dashboard: " + ", ".join(skipped) + "."]
    return "\n".join(out + (["", footer] if footer else []))


def _retry(call, what, tries=4, wait=5):
    err = None
    for i in range(tries):
        try:
            call().raise_for_status()
            return True
        except Exception as e:  # network blip or ntfy error
            err = e
            if i < tries - 1:
                time.sleep(wait * (i + 1))
    print(f"::warning::[notify] could not send {what} to ntfy after {tries} tries: {err}")
    return False


def send(title, text, buttons=(), url="", tags=("chart_with_upwards_trend",)):
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print("[notify] skipped: no NTFY_TOPIC")
        return False
    actions = [{"action": "view", "label": label, "url": f"{url}#{anchor}" if anchor else url, "clear": False}
               for label, anchor in buttons]
    body = {"topic": topic, "title": title, "message": text, "tags": list(tags), "click": url, "actions": actions}
    ok = _retry(lambda: requests.post("https://ntfy.sh/", timeout=20, json=body), title)
    if ok:
        print("[notify] sent")
    return ok


def send_file(path, title):
    """Upload a file (the weekly PDF) as its own note."""
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print("[notify] skipped: no NTFY_TOPIC")
        return False
    with open(path, "rb") as f:
        blob = f.read()
    return _retry(lambda: requests.put(f"https://ntfy.sh/{topic}", data=blob, timeout=60,
                                       headers={"Filename": os.path.basename(path), "Title": title}), title)


def sent_today(prefix, stamp, since="14h"):
    """True / False, or None when ntfy can't be asked (callers decide; a local marker is better when there is one)."""
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    for i in range(3):
        try:
            r = requests.get(f"https://ntfy.sh/{topic}/json", params={"poll": 1, "since": since}, timeout=20)
            r.raise_for_status()
            titles = [json.loads(line).get("title", "") for line in r.text.splitlines() if line.strip()]
            return any(t.startswith(prefix) and stamp in t for t in titles)
        except Exception:
            if i < 2:
                time.sleep(3 * (i + 1))
    return None

"""Phone notes via ntfy. The topic comes from the NTFY_TOPIC secret (never committed)."""
import json
import os

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


def send(title, text, buttons=(), url="", tags=("chart_with_upwards_trend",)):
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print("[notify] skipped: no NTFY_TOPIC")
        return False
    actions = [{"action": "view", "label": label, "url": f"{url}#{anchor}" if anchor else url, "clear": False}
               for label, anchor in buttons]
    requests.post("https://ntfy.sh/", timeout=20, json={"topic": topic, "title": title, "message": text,
                                                       "tags": list(tags), "click": url, "actions": actions})
    print("[notify] sent")
    return True


def sent_today(prefix, stamp, since="14h"):
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    try:
        r = requests.get(f"https://ntfy.sh/{topic}/json", params={"poll": 1, "since": since}, timeout=20)
        titles = [json.loads(line).get("title", "") for line in r.text.splitlines() if line.strip()]
        return any(t.startswith(prefix) and stamp in t for t in titles)
    except Exception:
        return False

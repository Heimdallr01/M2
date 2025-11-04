import os, re, json, hashlib, difflib, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

# === Konfiguration über Umgebungsvariablen (Settings → Actions → Variables) ===
TARGET_URL       = os.getenv("TARGET_URL")  # zu überwachende URL
CSS_SELECTOR     = os.getenv("CSS_SELECTOR", "").strip()     # optional
IGNORE_REGEX     = os.getenv("IGNORE_REGEX", "").strip()     # optional
USER_AGENT       = os.getenv("USER_AGENT", "ChangeWatcher/1.2 (+github actions)")

# ntfy (iOS/Android/Desktop)
NTFY_SERVER      = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC       = os.getenv("NTFY_TOPIC", "").strip()

# Eigener Nachrichtentext (Template). Platzhalter: {URL}, {TIME}, {DIFF}
NOTIFY_TEMPLATE  = os.getenv("NOTIFY_TEMPLATE", "")
NOTIFY_PREFIX    = os.getenv("NOTIFY_PREFIX", "🔔 Änderung erkannt")

STATE_FILE = Path("state.json")

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "last_hash": None,
        "last_excerpt": "",
        "updated_at": None,
        "etag": None,
        "last_modified": None,
        "last_ok_run": None
    }

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def send_ntfy(title: str, text: str):
    if not NTFY_TOPIC:
        return
    try:
        url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
        headers = {"Title": title, "Tags": "bell", "Priority": "4"}
        requests.post(url, data=text.encode("utf-8"), headers=headers, timeout=20)
    except Exception:
        pass

def build_message(url: str, diff_block: str) -> str:
    now = time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime())
    if NOTIFY_TEMPLATE:
        return (NOTIFY_TEMPLATE
                .replace("{URL}", url)
                .replace("{TIME}", now)
                .replace("{DIFF}", diff_block))
    return (f"{NOTIFY_PREFIX}\n"
            f"URL: {url}\n"
            f"Zeit: {now}\n\n"
            f"*Kurzdiff:*\n```\n{diff_block}\n```")

def fetch_content(url: str, etag: str | None, last_modified: str | None):
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 304:
        return None, etag, last_modified, True
    resp.raise_for_status()

    etag_new = resp.headers.get("ETag", etag)
    lm_new   = resp.headers.get("Last-Modified", last_modified)

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    if CSS_SELECTOR:
        node = soup.select_one(CSS_SELECTOR)
        text = node.get_text(separator="\n", strip=True) if node else soup.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    if IGNORE_REGEX:
        try:
            text = re.sub(IGNORE_REGEX, "", text)
        except re.error:
            pass

    text = re.sub(r"\s+\n", "\n", text).strip()
    return text, etag_new, lm_new, False

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def main():
    assert TARGET_URL, "TARGET_URL fehlt als Umgebungsvariable."

    state = load_state()
    content, etag, last_modified, not_modified = fetch_content(
        TARGET_URL, state.get("etag"), state.get("last_modified")
    )

    if not_modified:
        state["last_ok_run"] = int(time.time())
        save_state(state)
        return

    new_hash = sha256(content)
    old_hash = state.get("last_hash")

    if new_hash != old_hash:
        old = state.get("last_excerpt", "").splitlines()
        new = content.splitlines()
        diff_lines = list(difflib.unified_diff(old, new, lineterm="", n=5))
        pretty_diff = "\n".join(diff_lines[:60]) if diff_lines else "(Inhalt geändert)"

        text = build_message(TARGET_URL, pretty_diff)
        send_ntfy(os.getenv("NOTIFY_TITLE", "Ergebnis M2"), text)

        state["last_hash"] = new_hash
        state["last_excerpt"] = "\n".join(content.splitlines()[:400])
        state["updated_at"] = int(time.time())

    state["etag"] = etag
    state["last_modified"] = last_modified
    state["last_ok_run"] = int(time.time())
    save_state(state)

if __name__ == "__main__":
    main()

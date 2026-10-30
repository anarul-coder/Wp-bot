"""
Pure-Python WhatsApp AI Bot (using wars)
"""

import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, abort
from wars import WhatsApp

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("wa-bot")

ALLOWED_PHONE = re.sub(r"\D", "", os.getenv("ALLOWED_PHONE_NUMBER", ""))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip() or "https://render.com"
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()

# Default to /tmp for Render Free Tier compatibility
DB_FILE = Path(os.getenv("WARS_DB_FILE", "/tmp/whatsapp.db"))

MAX_INPUT_CHARS = 4000
RECENT_MESSAGE_IDS = deque(maxlen=300)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
state = {
    "status": "starting",
    "connected": False,
    "qr": None,
    "pair_code": None,
    "account": None,
    "error": None,
    "last_message": None,
    "messages_handled": 0,
}
state_lock = threading.Lock()

wa_ref = {"client": None}
start_lock = threading.Lock()
_started = {"value": False}

app = Flask(__name__, template_folder="templates")


def update_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def snapshot():
    with state_lock:
        return dict(state)


def normalize_number(jid: str) -> str:
    if not jid or "@" not in jid:
        return ""
    head = jid.split("@", 1)[0]
    head = head.split(":", 1)[0]
    return re.sub(r"\D", "", head)


def extract_text(msg) -> str:
    text = getattr(msg, "text", None)
    if text:
        return str(text).strip()
    return ""


def ask_openrouter(user_text: str) -> str:
    if not OPENROUTER_API_KEY:
        return "Bot is not fully configured: OPENROUTER_API_KEY is missing."
    if len(user_text) > MAX_INPUT_CHARS:
        user_text = user_text[:MAX_INPUT_CHARS]
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": PUBLIC_APP_URL,
                "X-Title": "Python WhatsApp AI Bot",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful WhatsApp assistant. Reply naturally and concisely. Match the language of the user (Bangla, English, or mixed)."},
                    {"role": "user", "content": user_text},
                ],
            },
            timeout=45,
        )
        if not resp.ok:
            log.error("OpenRouter HTTP %s: %s", resp.status_code, resp.text[:400])
            return f"AI service temporarily unavailable (HTTP {resp.status_code})."
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        log.exception("OpenRouter request failed")
        return "Sorry, I couldn't get an AI answer right now. Please try again shortly."


# ---------------------------------------------------------------------------
# WhatsApp background thread (using wars)
# ---------------------------------------------------------------------------
def start_whatsapp_background():
    with start_lock:
        if _started["value"]:
            return
        _started["value"] = True

    def thread_main():
        try:
            # Try to create the directory, fallback to /tmp if permission denied
            try:
                DB_FILE.parent.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                log.warning("Permission denied for %s. Falling back to /tmp", DB_FILE)
                global DB_FILE
                DB_FILE = Path("/tmp/whatsapp.db")

            log.info("Initializing wars client with DB: %s", DB_FILE)
            wa = WhatsApp(str(DB_FILE))
            wa_ref["client"] = wa

            # --- QR code callback ---
            @wa.on_qr
            def handle_qr(code: str):
                try:
                    from wars import qr_to_data_url
                    uri = qr_to_data_url(code)
                    update_state(status="waiting_for_qr", qr=uri, connected=False, error=None)
                    log.info("QR code generated. Waiting for scan.")
                except Exception as e:
                    log.exception("Failed to render QR")
                    update_state(error=f"QR render error: {e}")

            # --- Pairing code callback (optional) ---
            @wa.on_pair_code
            def handle_pair_code(code: str):
                update_state(pair_code=code, status="waiting_for_pair_code", qr=None)
                log.info("Pairing code generated: %s", code)

            # --- Connected callback ---
            @wa.on_connected
            def handle_connected():
                update_state(
                    status="online",
                    connected=True,
                    qr=None,
                    pair_code=None,
                    account=None,
                    error=None,
                )
                log.info("WhatsApp connected successfully.")

            # --- Disconnect callback ---
            @wa.on_disconnect
            def handle_disconnect():
                update_state(status="disconnected", connected=False)
                log.warning("WhatsApp disconnected. wars will attempt to reconnect.")

            # --- Message handler ---
            @wa.on_message
            def handle_message(msg):
                try:
                    if msg.is_from_me:
                        return

                    msg_id = getattr(msg, "id", None)
                    if msg_id:
                        if msg_id in RECENT_MESSAGE_IDS:
                            return
                        RECENT_MESSAGE_IDS.append(msg_id)

                    chat = getattr(msg, "chat", "") or ""
                    sender = normalize_number(chat)
                    if not ALLOWED_PHONE:
                        log.warning("ALLOWED_PHONE_NUMBER not set — ignoring all messages")
                        return
                    if sender != ALLOWED_PHONE:
                        log.info("Ignored message from %s", sender or "<unknown>")
                        return

                    text = extract_text(msg)
                    if not text:
                        return

                    update_state(last_message=f"Received {len(text)} chars from {sender}")
                    log.info("Handling message from %s (%d chars)", sender, len(text))

                    reply = ask_openrouter(text)
                    wa.send(msg.chat, reply)
                    with state_lock:
                        state["messages_handled"] += 1
                except Exception:
                    log.exception("handle_message failed")
                    update_state(error="Error while processing an incoming message.")

            update_state(status="connecting", error=None)
            log.info("Connecting wars client...")
            wa.connect()
            wa.wait_until_ready(timeout=120)
            log.info("wars client ready and running.")
            while True:
                time.sleep(10)

        except Exception as exc:
            log.exception("WhatsApp startup failed entirely")
            update_state(
                status="error",
                connected=False,
                error=f"Startup failed: {type(exc).__name__}: {exc}",
            )
        finally:
            with start_lock:
                _started["value"] = False

    threading.Thread(target=thread_main, name="whatsapp-client", daemon=True).start()


# ---------------------------------------------------------------------------
# Dashboard auth
# ---------------------------------------------------------------------------
def _authorized() -> bool:
    if not DASHBOARD_TOKEN:
        return True
    supplied = request.args.get("token") or request.headers.get("X-Dashboard-Token")
    return supplied == DASHBOARD_TOKEN


@app.before_request
def _gate_dashboard():
    if request.path in ("/health",):
        return
    if request.path.startswith("/static/"):
        return
    if not _authorized():
        abort(401)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    start_whatsapp_background()
    return render_template("dashboard.html")


@app.get("/api/status")
def api_status():
    start_whatsapp_background()
    return jsonify(snapshot())


@app.get("/health")
def health():
    return jsonify(ok=True, service="python-whatsapp-ai-bot")


if __name__ == "__main__":
    start_whatsapp_background()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

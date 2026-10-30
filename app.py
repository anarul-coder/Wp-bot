"""
Pure-Python WhatsApp AI Bot
"""

import asyncio
import base64
import logging
import os
import re
import threading
from collections import deque
from io import BytesIO
from pathlib import Path

import qrcode
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, abort

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

AUTH_FILE = Path(os.getenv("PIWAPP_AUTH_FILE", "/var/data/whatsapp/auth.json"))
KEYS_FILE = Path(str(AUTH_FILE) + ".keys")
DB_FILE = Path(str(AUTH_FILE) + ".db")

MAX_INPUT_CHARS = 4000
RECENT_MESSAGE_IDS = deque(maxlen=300)

state = {
    "status": "starting",
    "connected": False,
    "qr": None,
    "account": None,
    "error": None,
    "last_message": None,
    "messages_handled": 0,
}
state_lock = threading.Lock()

client_ref = {"client": None, "loop": None}
start_lock = threading.Lock()
_started = {"value": False}

app = Flask(__name__, template_folder="templates")


def update_state(**kwargs):
    with state_lock:
        state.update(kwargs)


def snapshot():
    with state_lock:
        return dict(state)


def qr_data_uri(value: str) -> str:
    img = qrcode.make(value)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def normalize_number(jid: str) -> str:
    if not jid or "@" not in jid:
        return ""
    head = jid.split("@", 1)[0]
    head = head.split(":", 1)[0]
    return re.sub(r"\D", "", head)


def extract_text(message: dict) -> str:
    if message.get("text"):
        return str(message["text"]).strip()
    msg = message.get("message") or {}
    candidates = [
        msg.get("conversation"),
        (msg.get("extendedTextMessage") or {}).get("text"),
        (msg.get("imageMessage") or {}).get("caption"),
        (msg.get("videoMessage") or {}).get("caption"),
        (msg.get("documentMessage") or {}).get("caption"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
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


def start_whatsapp_background():
    with start_lock:
        if _started["value"]:
            return
        _started["value"] = True

    def thread_main():
        try:
            try:
                from piwapp import Client, ConnectionConfig, AuthenticationCreds
                from piwapp.events import WAEventType
            except Exception as import_err:
                log.exception("Failed to import piwapp.")
                update_state(status="error", error=f"piwapp import failed: {import_err}")
                with start_lock:
                    _started["value"] = False
                return

            AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)

            # If the session file exists, try to load it. If it fails, delete it and start fresh.
            if AUTH_FILE.exists():
                try:
                    creds = AuthenticationCreds.from_json(AUTH_FILE.read_text(encoding="utf-8"))
                    log.info("Loaded existing session from %s", AUTH_FILE)
                except Exception as load_err:
                    log.warning("Failed to load session file, starting fresh: %s", load_err)
                    AUTH_FILE.unlink(missing_ok=True)
                    creds = AuthenticationCreds.initial()
                    AUTH_FILE.write_text(creds.to_json(), encoding="utf-8")
            else:
                creds = AuthenticationCreds.initial()
                AUTH_FILE.write_text(creds.to_json(), encoding="utf-8")
                log.info("Created new session file at %s", AUTH_FILE)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            def on_creds_update(c):
                try:
                    AUTH_FILE.write_text(c.to_json(), encoding="utf-8")
                except Exception:
                    log.exception("Failed to persist credentials")

            client = Client(
                creds,
                ConnectionConfig(),
                on_creds_update=on_creds_update,
                keys_path=str(KEYS_FILE),
                db_path=str(DB_FILE),
            )
            client_ref["client"] = client
            client_ref["loop"] = loop

            async def on_connection(update: dict):
                try:
                    if update.get("qr"):
                        uri = qr_data_uri(update["qr"])
                        update_state(status="waiting_for_qr", qr=uri, connected=False, error=None)
                        log.info("QR code generated. Waiting for scan.")
                    
                    conn = update.get("connection")
                    if conn == "open":
                        me = (update.get("me") or {}).get("id")
                        update_state(status="online", connected=True, qr=None, account=me, error=None)
                        log.info("WhatsApp connected as %s", me)
                    elif conn == "connecting":
                        update_state(status="connecting", error=None)
                    elif conn == "close":
                        # DO NOT restart manually. piwapp's loop handles reconnection.
                        update_state(status="disconnected", connected=False)
                        log.warning("WhatsApp connection closed. piwapp will attempt to reconnect automatically.")
                except Exception:
                    log.exception("connection.update handler failed")

            async def handle_message(client, message: dict):
                try:
                    key = message.get("key") or {}
                    if key.get("fromMe"):
                        return
                    msg_id = key.get("id")
                    if msg_id:
                        if msg_id in RECENT_MESSAGE_IDS:
                            return
                        RECENT_MESSAGE_IDS.append(msg_id)
                    remote_jid = key.get("remoteJid") or ""
                    if not remote_jid.endswith("@s.whatsapp.net"):
                        return
                    sender = normalize_number(key.get("participant") or remote_jid)
                    if not ALLOWED_PHONE:
                        log.warning("ALLOWED_PHONE_NUMBER not set — ignoring all messages")
                        return
                    if sender != ALLOWED_PHONE:
                        log.info("Ignored message from %s", sender or "<unknown>")
                        return
                    text = extract_text(message)
                    if not text:
                        return
                    update_state(last_message=f"Received {len(text)} chars from {sender}")
                    log.info("Handling message from %s (%d chars)", sender, len(text))
                    reply = await asyncio.to_thread(ask_openrouter, text)
                    await client.send_text(remote_jid, reply)
                    with state_lock:
                        state["messages_handled"] += 1
                except Exception:
                    log.exception("handle_message failed")
                    update_state(error="Error while processing an incoming message.")

            def on_messages(payload):
                messages = getattr(payload, "messages", None)
                if messages is None and isinstance(payload, dict):
                    messages = payload.get("messages", [])
                for msg in messages or []:
                    asyncio.run_coroutine_threadsafe(handle_message(client, msg), loop)

            client.on("connection.update", on_connection)
            client.events.on(WAEventType.MESSAGES_UPSERT, on_messages)

            update_state(status="connecting", error=None)
            log.info("Starting piwapp client loop...")
            loop.run_until_complete(client.start())

        except Exception as exc:
            log.exception("WhatsApp startup failed entirely")
            update_state(status="error", connected=False, error=f"Startup failed: {type(exc).__name__}")
        finally:
            with start_lock:
                _started["value"] = False

    threading.Thread(target=thread_main, name="whatsapp-client", daemon=True).start()


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

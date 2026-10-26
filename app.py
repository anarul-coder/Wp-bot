import os
import re
import json
import asyncio
import threading
import base64
from io import BytesIO
from pathlib import Path

import requests
import qrcode
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string

load_dotenv()
app = Flask(__name__)

ALLOWED_PHONE = re.sub(r"\D", "", os.getenv("ALLOWED_PHONE_NUMBER", ""))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
AUTH_FILE = Path(os.getenv("PIWAPP_AUTH_FILE", "/var/data/piwapp_auth.json"))
KEYS_FILE = Path(str(AUTH_FILE) + ".keys")
DB_FILE = Path(str(AUTH_FILE) + ".db")

state = {"connected": False, "qr": None, "message": "Starting WhatsApp client…"}
client_ref = {"client": None, "loop": None}
start_lock = threading.Lock()

HTML = """
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp Python QR Bot</title>
<style>
body{font-family:system-ui,sans-serif;max-width:680px;margin:32px auto;padding:0 16px;background:#f5f7f8;color:#18212b}
main{background:white;padding:24px;border-radius:16px;box-shadow:0 3px 18px #0001}
h1{font-size:1.5rem}#qr{max-width:300px;width:100%;image-rendering:auto}
.status{padding:12px;border-radius:8px;background:#eef4f6;margin:12px 0}
small{color:#5c6670}.refresh{margin-top:14px;padding:10px 16px}
</style></head><body><main>
<h1>WhatsApp AI Bot — Python QR Login</h1>
<div class="status" id="status">Loading status…</div>
<div id="qrbox"><p>Waiting for QR code…</p></div>
<p><small>On your phone: WhatsApp → Linked devices → Link a device → scan this QR.</small></p>
<button class="refresh" onclick="refresh()">Refresh status</button>
</main>
<script>
async function refresh(){
  try {
    const r=await fetch('/api/status'); const d=await r.json();
    document.getElementById('status').textContent=d.message || (d.connected?'Connected':'Waiting for connection');
    const box=document.getElementById('qrbox');
    if(d.connected){box.innerHTML='<p>✅ WhatsApp is connected.</p>';}
    else if(d.qr){box.innerHTML='<img id="qr" alt="WhatsApp QR code" src="'+d.qr+'">';}
    else {box.innerHTML='<p>Waiting for QR code… refresh in a few seconds.</p>';}
  } catch(e){document.getElementById('status').textContent='Could not get status. Check Render logs.';}
}
refresh(); setInterval(refresh,3000);
</script></body></html>
"""

def save_qr_data_uri(qr_value):
    if not qr_value:
        return None
    try:
        img = qrcode.make(str(qr_value))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        print("QR render error:", repr(exc))
        return None

def ask_openrouter(message):
    if not OPENROUTER_API_KEY:
        return "Bot setup incomplete: OPENROUTER_API_KEY is missing."
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("PUBLIC_APP_URL", "https://render.com"),
                "X-Title": "Python WhatsApp AI Bot",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful WhatsApp assistant. Reply briefly in the language used by the sender, including Bangla or English."},
                    {"role": "user", "content": message},
                ],
            },
            timeout=45,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print("OpenRouter error:", repr(exc))
        return "দুঃখিত, AI উত্তর দিতে পারছে না। একটু পরে আবার চেষ্টা করুন।"

def sender_phone(jid):
    if not jid or not jid.endswith("@s.whatsapp.net"):
        return ""
    return re.sub(r"\D", "", jid.split("@", 1)[0])

def start_whatsapp_thread():
    with start_lock:
        if client_ref["loop"] is not None:
            return

        def runner():
            try:
                from piwapp import Client, ConnectionConfig, AuthenticationCreds
                from piwapp.events import WAEventType

                AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)

                if AUTH_FILE.exists():
                    creds = AuthenticationCreds.from_json(AUTH_FILE.read_text())
                else:
                    creds = AuthenticationCreds.initial()
                    AUTH_FILE.write_text(creds.to_json())

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                client_ref["loop"] = loop

                async def on_connection(update):
                    if "qr" in update:
                        state["qr"] = save_qr_data_uri(update["qr"])
                        state["connected"] = False
                        state["message"] = "Scan the QR code with WhatsApp Linked Devices."
                    if update.get("connection") == "open":
                        state["connected"] = True
                        state["qr"] = None
                        state["message"] = "WhatsApp connected successfully."

                async def handle_message(m):
                    try:
                        key = m.get("key") or {}
                        if key.get("fromMe"):
                            return
                        jid = key.get("remoteJid", "")
                        phone = sender_phone(jid)
                        text = (m.get("text") or "").strip()
                        if not phone or not text:
                            return
                        if not ALLOWED_PHONE or phone != ALLOWED_PHONE:
                            print("Ignored message from a non-allowed number.")
                            return
                        client = client_ref["client"]
                        reply = await asyncio.to_thread(ask_openrouter, text)
                        await client.send_text(jid, reply)
                    except Exception as exc:
                        print("Message handling error:", repr(exc))

                def on_messages(payload):
                    messages = getattr(payload, "messages", None)
                    if messages is None and isinstance(payload, dict):
                        messages = payload.get("messages", [])
                    for m in messages or []:
                        asyncio.run_coroutine_threadsafe(handle_message(m), loop)

                client = Client(
                    creds,
                    ConnectionConfig(),
                    on_creds_update=lambda c: AUTH_FILE.write_text(c.to_json()),
                    keys_path=str(KEYS_FILE),
                    db_path=str(DB_FILE),
                )
                client_ref["client"] = client
                client.on("connection.update", on_connection)
                client.events.on(WAEventType.MESSAGES_UPSERT, on_messages)

                state["message"] = "Connecting to WhatsApp…"
                loop.run_until_complete(client.start())

            except Exception as exc:
                state["connected"] = False
                state["message"] = "WhatsApp startup error: " + str(exc)
                print("WhatsApp startup error:", repr(exc))

        threading.Thread(target=runner, daemon=True).start()

@app.get("/")
def index():
    start_whatsapp_thread()
    return render_template_string(HTML)

@app.get("/api/status")
def api_status():
    start_whatsapp_thread()
    return jsonify(state)

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "python-only-whatsapp-qr-bot"})

if __name__ == "__main__":
    start_whatsapp_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

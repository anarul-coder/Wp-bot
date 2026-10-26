import os
import re
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request

load_dotenv()
app = Flask(__name__)

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3001").rstrip("/")
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
# Put the one allowed WhatsApp number in .env, country code included, digits only.
ALLOWED_PHONE_NUMBER = re.sub(r"\D", "", os.getenv("ALLOWED_PHONE_NUMBER", ""))

def bridge_headers():
    return {"X-Bridge-Secret": BRIDGE_SECRET}

def openrouter_reply(message):
    if not OPENROUTER_API_KEY:
        return "The bot owner has not configured OPENROUTER_API_KEY yet."
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("PUBLIC_APP_URL", "https://render.com"),
                "X-Title": "Private WhatsApp AI Bot"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful WhatsApp assistant. Answer the user's questions "
                            "clearly and concisely. The user may write in Bangla or English; "
                            "reply in the language they use."
                        )
                    },
                    {"role": "user", "content": message}
                ]
            },
            timeout=45
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print("OpenRouter request failed:", repr(exc))
        return "Sorry, I could not get an AI response right now. Please try again later."

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/status")
def status():
    try:
        r = requests.get(f"{BRIDGE_URL}/status", headers=bridge_headers(), timeout=8)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"connected": False, "state": "bridge_unavailable",
                        "message": "WhatsApp bridge is not reachable"}), 503

@app.get("/api/qr")
def qr():
    try:
        r = requests.get(f"{BRIDGE_URL}/qr", headers=bridge_headers(), timeout=8)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"qr": None, "message": "WhatsApp bridge is not reachable"}), 503

@app.post("/api/logout")
def logout():
    try:
        r = requests.post(f"{BRIDGE_URL}/logout", headers=bridge_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"ok": False, "message": "WhatsApp bridge is not reachable"}), 503

@app.post("/api/incoming")
def incoming():
    # This endpoint is called by the private Node bridge. Check shared secret.
    if not BRIDGE_SECRET or request.headers.get("X-Bridge-Secret") != BRIDGE_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    sender = re.sub(r"\D", "", str(data.get("sender", "")))
    message = str(data.get("message", "")).strip()

    if not sender or not message:
        return jsonify({"ok": True, "ignored": "empty message"}), 200

    # Only the configured number gets an AI reply. Everyone else is ignored.
    if not ALLOWED_PHONE_NUMBER or sender != ALLOWED_PHONE_NUMBER:
        print(f"Ignored message from non-allowed sender: {sender}")
        return jsonify({"ok": True, "ignored": "sender_not_allowed"}), 200

    reply = openrouter_reply(message)
    try:
        r = requests.post(
            f"{BRIDGE_URL}/send",
            headers={**bridge_headers(), "Content-Type": "application/json"},
            json={"to": sender, "message": reply},
            timeout=30
        )
        r.raise_for_status()
        return jsonify({"ok": True}), 200
    except Exception as exc:
        print("Could not send WhatsApp reply:", repr(exc))
        return jsonify({"ok": False, "error": "send_failed"}), 502

@app.get("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

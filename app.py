
import os
import re
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request

load_dotenv()
app = Flask(__name__)

BRIDGE_URL = os.getenv(
    "WHATSAPP_BRIDGE_URL", "http://localhost:3001"
).rstrip("/")

BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

ALLOWED_PHONE_NUMBER = re.sub(
    r"\D", "", os.getenv("ALLOWED_PHONE_NUMBER", "")
)


def bridge_headers():
    return {"X-Bridge-Secret": BRIDGE_SECRET}


def openrouter_reply(message):
    if not OPENROUTER_API_KEY:
        return "Bot owner has not configured the OpenRouter API key."

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv(
                    "PUBLIC_APP_URL", "https://render.com"
                ),
                "X-Title": "Private WhatsApp AI Bot",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful WhatsApp assistant. "
                            "Reply clearly and concisely in the language "
                            "the user uses, including Bangla or English."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
            },
            timeout=45,
        )

        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except Exception as exc:
        print("OpenRouter error:", repr(exc))
        return "Sorry, AI is temporarily unavailable. Please try again."


@app.get("/")
def index():
    # Main dashboard page
    try:
        return render_template("index.html")
    except Exception as exc:
        print("Dashboard template error:", repr(exc))
        return (
            "<h2>WhatsApp AI Bot Dashboard</h2>"
            "<p>The Python service is running.</p>"
            "<p>Ensure templates/index.html exists in your repository.</p>"
            '<p><a href="/health">Check service health</a></p>',
            200,
        )


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "python-dashboard"}), 200


@app.get("/api/status")
def status():
    try:
        response = requests.get(
            f"{BRIDGE_URL}/status",
            headers=bridge_headers(),
            timeout=8,
        )
        return jsonify(response.json()), response.status_code
    except Exception as exc:
        print("Bridge status error:", repr(exc))
        return jsonify({
            "connected": False,
            "state": "bridge_unavailable",
            "message": "WhatsApp bridge is not reachable.",
        }), 503


@app.get("/api/qr")
def qr():
    try:
        response = requests.get(
            f"{BRIDGE_URL}/qr",
            headers=bridge_headers(),
            timeout=8,
        )
        return jsonify(response.json()), response.status_code
    except Exception as exc:
        print("Bridge QR error:", repr(exc))
        return jsonify({
            "qr": None,
            "connected": False,
            "message": "WhatsApp bridge is not reachable.",
        }), 503


@app.post("/api/logout")
def logout():
    try:
        response = requests.post(
            f"{BRIDGE_URL}/logout",
            headers=bridge_headers(),
            timeout=10,
        )
        return jsonify(response.json()), response.status_code
    except Exception as exc:
        print("Bridge logout error:", repr(exc))
        return jsonify({
            "ok": False,
            "message": "WhatsApp bridge is not reachable.",
        }), 503


@app.post("/api/incoming")
def incoming():
    # Accept requests only from the trusted Node.js bridge.
    supplied_secret = request.headers.get("X-Bridge-Secret", "")

    if not BRIDGE_SECRET or supplied_secret != BRIDGE_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    sender = re.sub(r"\D", "", str(data.get("sender", "")))
    message = str(data.get("message", "")).strip()

    if not sender or not message:
        return jsonify({"ok": True, "ignored": "empty_message"}), 200

    # Reply only to the one number configured in the environment.
    if not ALLOWED_PHONE_NUMBER or sender != ALLOWED_PHONE_NUMBER:
        print("Ignored message from non-allowed sender.")
        return jsonify({
            "ok": True,
            "ignored": "sender_not_allowed",
        }), 200

    reply = openrouter_reply(message)

    try:
        response = requests.post(
            f"{BRIDGE_URL}/send",
            headers={
                **bridge_headers(),
                "Content-Type": "application/json",
            },
            json={"to": sender, "message": reply},
            timeout=30,
        )
        response.raise_for_status()
        return jsonify({"ok": True}), 200

    except Exception as exc:
        print("WhatsApp send error:", repr(exc))
        return jsonify({
            "ok": False,
            "error": "send_failed",
        }), 502


@app.errorhandler(404)
def not_found(_error):
    return jsonify({
        "error": "not_found",
        "message": "This URL does not exist. Open the dashboard at /",
    }), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# Private WhatsApp AI Bot — QR dashboard + allowlisted number

## What this does
- Shows a QR code on an HTML dashboard for linking a WhatsApp Web session.
- Receives private text messages through a separate Node.js Baileys bridge.
- Replies using OpenRouter.
- Replies ONLY when the sender's number matches `ALLOWED_PHONE_NUMBER` in `.env`.
- Ignores all other senders.
- Uses Python/Flask for the dashboard, allowlist, and OpenRouter requests.

## Configure `.env`
Copy `.env.example` to `.env` and fill in:
- `ALLOWED_PHONE_NUMBER`: your one allowed WhatsApp number in international format, digits only, no plus sign. Example format: `8801XXXXXXXXX`.
- `OPENROUTER_API_KEY`: your secret API key.
- `BRIDGE_SECRET`: long random shared secret.
- Local URLs shown in the example are fine for local testing.

Do not commit `.env` or share your API key. `.gitignore` excludes `.env`.

## Run locally
Terminal 1:
```bash
npm install
npm start
```
Terminal 2:
```bash
python -m venv .venv
# activate the virtual environment, then:
pip install -r requirements.txt
python app.py
```
Open `http://localhost:10000`. Scan the QR using WhatsApp > Linked devices > Link a device.
Make sure both processes load the same `.env` (Node and Python use dotenv).

## Render
Deploy the Python dashboard and Node bridge as two services. Set the environment variables in each service's Render dashboard. Use the actual private/internal service URLs Render provides; replace the sample URLs if needed. Set `BRIDGE_SECRET` to the exact same value in both services. Set `ALLOWED_PHONE_NUMBER` and `OPENROUTER_API_KEY` on the Python service. A persistent disk for the Node service is needed to retain the WhatsApp session; check Render's current plan requirements and pricing.

## Important
This is an unofficial WhatsApp Web integration, not the official WhatsApp Cloud API. It may break or violate WhatsApp terms; the official Cloud API is preferable for production/business use. Keep the QR dashboard private, since anyone who scans the QR can link a device. The code filters incoming messages to one configured number before sending any message to OpenRouter, and only replies to that number.

# Python WhatsApp AI Bot

Pure-Python WhatsApp bot:
- **Flask** dashboard with QR login
- **piwapp** (unofficial WhatsApp Web client)
- **OpenRouter** for AI replies
- No Node.js. No bridge service.

## Deploy on Render

1. Push this repo to GitHub.
2. Create a **Web Service** on Render.
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
5. Add a **persistent disk** mounted at `/var/data` (paid plan required).
6. Set environment variables:
   - `ALLOWED_PHONE_NUMBER` — digits only, e.g. `88017XXXXXXXX`
   - `OPENROUTER_API_KEY`
   - `OPENROUTER_MODEL` — default `openrouter/free`
   - `PUBLIC_APP_URL` — your Render URL
   - `PIWAPP_AUTH_FILE` — `/var/data/whatsapp/auth.json`
   - `DASHBOARD_TOKEN` — a long random string (recommended)
7. Deploy, open the URL (`?token=YOUR_TOKEN`), scan the QR.

## Important rules

- **One worker only.** `piwapp` cannot share a session across processes.
- **Persistent disk is required** for the login to survive restarts.
  Without it, you must re-scan the QR after every redeploy or idle spin-down.
- **Keep the dashboard URL secret.** The QR allows anyone to link a device.
- **Only the sender matching `ALLOWED_PHONE_NUMBER` gets AI replies.** All other
  numbers and all groups are ignored.
- `piwapp` is **unofficial**. WhatsApp may change protocol and break it, or
  restrict accounts. Use a spare number. Follow WhatsApp's Terms of Service.

## Local test

```bash
cp .env.example .env
# edit .env with your values
pip install -r requirements.txt
python app.py
# open http://localhost:10000

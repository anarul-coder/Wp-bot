# Python-only WhatsApp QR AI Bot

This starter uses `piwapp`, a pure-Python WhatsApp Web client, Flask, and OpenRouter. No JavaScript/Node.js bridge is used.

## Render settings

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Python version: 3.12 (provided by `.python-version`)

## Environment variables

Set these in Render:
- `ALLOWED_PHONE_NUMBER`: only this sender can receive AI replies, digits only, international format, e.g. `88017XXXXXXXX`
- `OPENROUTER_API_KEY`: your API key
- `OPENROUTER_MODEL`: `openrouter/free`
- `PUBLIC_APP_URL`: your actual Render URL
- `PIWAPP_AUTH_FILE`: `/var/data/piwapp_auth.json`

## Persistent storage

Add a persistent disk to the Render service with mount path `/var/data`. WhatsApp auth credentials and keys must persist across restarts. Persistent disks may require a paid Render plan. Without persistent storage, the login session may be lost after restart/redeploy.

## Pairing

Open the app URL and scan the QR displayed by the dashboard:
WhatsApp > Linked devices > Link a device.

## Notes

- This uses an unofficial WhatsApp Web protocol client; use at your own risk and follow WhatsApp's terms.
- Test with a spare account first.
- The WhatsApp QR connection runs in the same Python process as Flask; avoid multiple instances of this service.
- `piwapp` requires Python 3.12+ and its APIs may change as the project evolves.

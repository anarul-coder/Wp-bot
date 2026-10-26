require('dotenv').config();
const express = require('express');
const QRCode = require('qrcode');
const pino = require('pino');
const fs = require('fs');
const axios = require('axios');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason
} = require('@whiskeysockets/baileys');

const app = express();
app.use(express.json({ limit: '1mb' }));

const PORT = Number(process.env.PORT || 3001);
const SECRET = process.env.BRIDGE_SECRET || '';
const AUTH_DIR = process.env.AUTH_DIR || './auth_info';
const PYTHON_APP_URL = (process.env.PYTHON_APP_URL || 'http://localhost:10000').replace(/\/$/, '');
let sock = null;
let latestQr = null;
let state = 'starting';
let connected = false;
let starting = false;

app.use((req, res, next) => {
  if (!SECRET || req.header('X-Bridge-Secret') !== SECRET) {
    return res.status(401).json({ error: 'Unauthorized bridge request' });
  }
  next();
});

async function startWhatsApp() {
  if (starting) return;
  starting = true;
  try {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    const { state: authState, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    sock = makeWASocket({
      auth: authState,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      browser: ['Private Video AI Bot', 'Chrome', '1.0.0']
    });
    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;
      if (qr) {
        latestQr = await QRCode.toDataURL(qr);
        state = 'qr_ready';
        connected = false;
      }
      if (connection === 'open') {
        connected = true;
        latestQr = null;
        state = 'connected';
      }
      if (connection === 'close') {
        connected = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        state = 'disconnected';
        if (code !== DisconnectReason.loggedOut) {
          setTimeout(() => {
            starting = false;
            startWhatsApp().catch(console.error);
          }, 2500);
        } else {
          latestQr = null;
          state = 'logged_out';
          starting = false;
        }
      }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
      if (type !== 'notify') return;
      for (const msg of messages || []) {
        if (!msg.message || msg.key.fromMe || msg.key.remoteJid === 'status@broadcast') continue;
        // This starter handles private text messages only, not groups or media.
        const jid = msg.key.remoteJid || '';
        if (!jid.endsWith('@s.whatsapp.net')) continue;
        const sender = jid.split('@')[0].split(':')[0];
        const content = msg.message.conversation
          || msg.message.extendedTextMessage?.text
          || msg.message.imageMessage?.caption
          || msg.message.videoMessage?.caption
          || '';
        if (!content.trim()) continue;
        try {
          await axios.post(`${PYTHON_APP_URL}/api/incoming`,
            { sender, message: content.trim() },
            { headers: { 'X-Bridge-Secret': SECRET }, timeout: 60000 });
        } catch (err) {
          console.error('Could not forward incoming message to Python app:', err.message);
        }
      }
    });
  } catch (e) {
    state = 'error';
    console.error('WhatsApp bridge start failed:', e);
    starting = false;
  }
}

app.get('/status', (_req, res) => res.json({ connected, state }));
app.get('/qr', (_req, res) => res.json({ qr: latestQr, connected, state }));

app.post('/send', async (req, res) => {
  const to = String(req.body?.to || '').replace(/\D/g, '');
  const message = String(req.body?.message || '').trim();
  if (!to || !message) return res.status(400).json({ error: 'to and message required' });
  if (!connected || !sock) return res.status(503).json({ error: 'WhatsApp is not connected' });
  try {
    await sock.sendMessage(`${to}@s.whatsapp.net`, { text: message });
    res.json({ ok: true });
  } catch (e) {
    console.error('Send failed:', e);
    res.status(500).json({ ok: false, error: 'send_failed' });
  }
});

app.post('/logout', async (_req, res) => {
  try {
    if (sock) await sock.logout();
    connected = false;
    latestQr = null;
    state = 'logged_out';
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, message: 'Logout failed' });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`WhatsApp bridge listening on ${PORT}`);
  startWhatsApp();
});

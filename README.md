# Autonomous IVR Navigation Agent

This project is an autonomous phone-call assistant for real-world IVR systems.

It places an outbound call, listens to the live menu audio, transcribes what the IVR is saying, and makes real-time decisions (DTMF, wait, handoff, patch user in, hang up). When a human representative is detected, it can generate a handoff message and bridge the user into the same call.

Built on FastAPI + Twilio Media Streams with an explicit state machine, this repo is designed to run live telephony sessions end-to-end from a single dashboard.

## What It Does

- Starts and controls outbound IVR calls with Twilio.
- Ingests streaming call audio over WebSocket.
- Uses Vosk ASR + WebRTC VAD for live call-state classification.
- Runs a planner loop to choose one safe action at a time.
- Bridges user + business call legs via conference when ready.
- Tracks session metrics (`/api/metrics`) including saved hold time and systems covered.

## Project Structure

```
Caller/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
└── app/
    ├── main.py               # API routes + orchestration loop
    ├── agent.py              # ASR/VAD + planner + action sanitization
    ├── state_machine.py      # explicit call state machine
    ├── metrics.py            # KPI aggregation
    ├── telephony.py          # Twilio helpers + TwiML builders
    ├── tts.py                # ElevenLabs helper
    ├── audio.py              # audio decode/chunk utils
    ├── static/app.js         # dashboard client logic
    ├── templates/index.html  # dashboard UI
    └── models/               # local Vosk model files
```

## Requirements

- Python 3.11+
- Twilio account + phone number
- Public HTTPS URL reachable by Twilio
- Gemini API key
- Optional: ElevenLabs API key + voice ID

## Local Setup

```bash
cd /Users/mustafa/Desktop/Uni/Caller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with:

- `PUBLIC_BASE_URL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`
- `GEMINI_API_KEY`
- optional: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `VOSK_MODEL_PATH`

## Run

```bash
cd /Users/mustafa/Desktop/Uni/Caller
uvicorn app.main:app --reload --port 8000
```

Open: `http://127.0.0.1:8000`

## Main Endpoints

- `GET /` dashboard
- `POST /api/start` start session
- `POST /api/stop` stop session
- `GET /api/status` session + state snapshot
- `GET /api/metrics` KPI summary
- `GET /health` readiness check
- `POST /twiml/outbound` TwiML for business leg
- `POST /twiml/join_user` TwiML for user leg
- `GET /audio/handoff.mp3` generated handoff audio
- `WS /ws/ui` dashboard log stream
- `WS /ws/media` Twilio media ingress

## Deploy (Any Python Host)

- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `PUBLIC_BASE_URL` to your deployed HTTPS URL

## Git Safety

- `.gitignore` excludes `.env`, virtualenvs, caches, logs, and key/cert files.
- Keep `.env.example` in git; never commit `.env`.

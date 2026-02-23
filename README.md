# Phantom Secretary

Autonomous voice agent for real-time IVR navigation and human handoff.

## Delivered Capabilities

- Autonomous call flow that dials a target IVR, listens, navigates menus with DTMF, detects humans, and patches the user in.
- Explicit ASR+VAD call-state machine (`LISTENING`, `MENU`, `HOLD`, `HUMAN_DETECTED`, `HANDOFF_READY`, `PATCHING_USER`, `BRIDGED`, `FINISHED`).
- Concurrent orchestration of:
  - Twilio Media Streams WebSocket ingestion
  - Twilio call/conference control
  - Gemini planning calls
  - ElevenLabs TTS generation
  against shared session state with lock-based coordination.
- Production-style KPI tracking (`/api/metrics`) for:
  - distinct IVR systems covered
  - autonomous sessions
  - average saved hold time
  - target gate: `goal_15m_10systems_met`

## Bullet-Point Alignment

This codebase now directly supports the requested claims:

1. **Autonomous voice agent over unpredictable IVR systems**
- Implemented end-to-end observe -> plan -> act loop in `/Users/mustafa/Desktop/Uni/Caller/backend/app/main.py` and `/Users/mustafa/Desktop/Uni/Caller/backend/app/agent.py`.
- Tracks system-level coverage and saved-hold KPIs in `/Users/mustafa/Desktop/Uni/Caller/backend/app/metrics.py`.

2. **FastAPI backend with explicit call-state machine using ASR + VAD**
- Explicit machine in `/Users/mustafa/Desktop/Uni/Caller/backend/app/state_machine.py`.
- Live audio classification driven by Vosk ASR transcript + WebRTC VAD speech ratio in `/Users/mustafa/Desktop/Uni/Caller/backend/app/main.py`.

3. **Concurrent WebSocket ingestion, conference bridging, and multi-service APIs under real-time constraints**
- Media handling in `WS /ws/media`.
- Non-blocking external service calls via `asyncio.to_thread` for Twilio, Gemini, ElevenLabs.
- Shared-state coordination through `SESSION_LOCK` in `/Users/mustafa/Desktop/Uni/Caller/backend/app/main.py`.

## Project Layout

- `/Users/mustafa/Desktop/Uni/Caller/backend/app/main.py`: FastAPI orchestrator, session lifecycle, real-time loop.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/state_machine.py`: explicit call-state machine.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/agent.py`: ASR, VAD, planner, safe action sanitizer.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/metrics.py`: KPI/session metrics store.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/telephony.py`: Twilio helpers and TwiML builders.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/templates/index.html`: Netflix-inspired animated dashboard.
- `/Users/mustafa/Desktop/Uni/Caller/backend/app/static/app.js`: dashboard logic, live telemetry polling.

## Prerequisites

- Python 3.11+
- Twilio account + Twilio phone number
- Public HTTPS URL reachable by Twilio (ngrok for local development)
- Gemini API key
- Optional: ElevenLabs API key + voice id

## Setup

1. Install dependencies:

```bash
cd /Users/mustafa/Desktop/Uni/Caller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create env file:

```bash
cp /Users/mustafa/Desktop/Uni/Caller/backend/.env.example /Users/mustafa/Desktop/Uni/Caller/backend/.env
```

3. Fill required values in `/Users/mustafa/Desktop/Uni/Caller/backend/.env`:
- `PUBLIC_BASE_URL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`
- `GEMINI_API_KEY`

## Run

```bash
cd /Users/mustafa/Desktop/Uni/Caller/backend
uvicorn app.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## API Endpoints

- `GET /`: control-room dashboard
- `POST /api/start`: start an autonomous call session
- `POST /api/stop`: stop active session and hang up
- `GET /api/status`: live status, state machine snapshot, config status
- `GET /api/metrics`: KPI summary across sessions
- `GET /health`: startup readiness checks
- `POST /twiml/outbound`: TwiML for business leg (stream + conference)
- `POST /twiml/join_user`: TwiML for user patch-in leg
- `GET /audio/handoff.mp3`: handoff audio asset
- `WS /ws/ui`: live log stream to UI
- `WS /ws/media`: Twilio Media Streams ingress

## Verifying the KPI Claim

Use `GET /api/metrics` to monitor progress toward target outcomes (for example, average saved minutes and number of IVR systems covered).

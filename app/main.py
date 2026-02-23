import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, List, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from app.agent import IVRAgent
from app.audio import twilio_ulaw_b64_to_pcm16, rms_energy, chunk_bytes
from app.metrics import MetricsStore, SessionKPI
from app.state_machine import CallStateMachine
from app.telephony import Telephony, TelephonyConfig, build_twiml_outbound, build_twiml_join_user
from app.tts import elevenlabs_tts_mp3


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

load_dotenv(PROJECT_DIR / ".env")
load_dotenv()

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

DEFAULT_VOSK_MODEL_PATH = APP_DIR / "models" / "vosk" / "vosk-model-small-en-us-0.15"
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", str(DEFAULT_VOSK_MODEL_PATH))

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _build_ws_media_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :] + "/ws/media"
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :] + "/ws/media"
    return ""


def _fmt_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60.0:.2f}m"


WS_MEDIA_URL = _build_ws_media_url(PUBLIC_BASE_URL)

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

telephony = Telephony(
    TelephonyConfig(
        account_sid=TWILIO_ACCOUNT_SID,
        auth_token=TWILIO_AUTH_TOKEN,
        from_number=TWILIO_FROM_NUMBER,
        public_base_url=PUBLIC_BASE_URL,
    )
)
agent = IVRAgent(gemini_key=GEMINI_API_KEY, vosk_model_path=VOSK_MODEL_PATH)
state_machine = CallStateMachine()
metrics = MetricsStore()


@dataclass
class Session:
    active: bool = False

    target_number: str = ""
    user_phone: str = ""
    goal_state: str = ""
    call_reason: str = ""
    ivr_system: str = ""

    conference_name: str = "phantom-conf"

    business_call_sid: Optional[str] = None
    user_call_sid: Optional[str] = None

    handoff_mp3: Optional[bytes] = None

    said_handoff: bool = False
    patched_user: bool = False

    started_at: Optional[float] = None
    last_end_reason: str = ""


SESSION = Session()
SESSION_LOCK = asyncio.Lock()


class StartPayload(BaseModel):
    target_number: str
    user_phone: str
    goal_state: str
    call_reason: str

    @field_validator("target_number", "user_phone", "goal_state", "call_reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


UI_CLIENTS: Set[WebSocket] = set()


async def ui_broadcast(msg: str) -> None:
    dead = []
    ts = time.strftime("%H:%M:%S")
    line = f"{ts} - {msg}"
    for ws in list(UI_CLIENTS):
        try:
            await ws.send_text(line)
        except Exception:
            dead.append(ws)
    for ws in dead:
        UI_CLIENTS.discard(ws)


def _start_config_errors() -> List[str]:
    errors: List[str] = []
    if not PUBLIC_BASE_URL.startswith(("https://", "http://")):
        errors.append("PUBLIC_BASE_URL must start with https:// or http://.")
    if not WS_MEDIA_URL:
        errors.append("Could not derive WS media URL from PUBLIC_BASE_URL.")
    if not TWILIO_ACCOUNT_SID:
        errors.append("Missing TWILIO_ACCOUNT_SID.")
    if not TWILIO_AUTH_TOKEN:
        errors.append("Missing TWILIO_AUTH_TOKEN.")
    if not TWILIO_FROM_NUMBER:
        errors.append("Missing TWILIO_FROM_NUMBER.")
    if not Path(VOSK_MODEL_PATH).exists():
        errors.append(f"Vosk model path does not exist: {VOSK_MODEL_PATH}")
    return errors


def _start_payload_errors(payload: StartPayload) -> List[str]:
    errors: List[str] = []
    if not payload.target_number or not E164_RE.match(payload.target_number):
        errors.append("target_number must be valid (example: +14155551234).")
    if not payload.user_phone or not E164_RE.match(payload.user_phone):
        errors.append("user_phone must be valid (example: +14155559876).")
    if not payload.goal_state:
        errors.append("goal_state is required.")
    if not payload.call_reason:
        errors.append("call_reason is required.")
    return errors


def _build_status_payload() -> Dict[str, Any]:
    return {
        "ok": True,
        "session": {
            "active": SESSION.active,
            "target_number": SESSION.target_number,
            "user_phone": SESSION.user_phone,
            "goal_state": SESSION.goal_state,
            "call_reason": SESSION.call_reason,
            "ivr_system": SESSION.ivr_system,
            "conference_name": SESSION.conference_name,
            "business_call_sid": SESSION.business_call_sid,
            "user_call_sid": SESSION.user_call_sid,
            "said_handoff": SESSION.said_handoff,
            "patched_user": SESSION.patched_user,
            "started_at": SESSION.started_at,
            "last_end_reason": SESSION.last_end_reason,
        },
        "state_machine": state_machine.snapshot(),
        "metrics": metrics.summary(),
        "config": {
            "public_base_url_set": bool(PUBLIC_BASE_URL),
            "twilio_config_set": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER),
            "gemini_enabled": bool(GEMINI_API_KEY),
            "elevenlabs_enabled": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
            "vosk_model_path": VOSK_MODEL_PATH,
            "vosk_model_exists": Path(VOSK_MODEL_PATH).exists(),
            "ws_media_url": WS_MEDIA_URL,
        },
    }


async def _finalize_session(reason: str) -> Optional[SessionKPI]:
    async with SESSION_LOCK:
        if not SESSION.active:
            return None

        now = time.time()
        state_machine.finish(reason)
        snapshot = state_machine.snapshot()

        started_at = SESSION.started_at or now
        runtime_seconds = max(0.0, now - started_at)

        kpi = SessionKPI(
            ivr_system=SESSION.ivr_system or "unknown",
            started_at=started_at,
            ended_at=now,
            runtime_seconds=runtime_seconds,
            hold_seconds=float(snapshot["hold_seconds"]),
            digits_pressed=len(agent.mem.pressed_digits),
            actions_count=len(agent.mem.recent_actions),
            patched_user=SESSION.patched_user,
            human_detected=agent.mem.human_detected,
            ended_reason=reason,
        )
        metrics.record(kpi)

        SESSION.active = False
        SESSION.last_end_reason = reason
        SESSION.business_call_sid = None
        SESSION.user_call_sid = None
        SESSION.handoff_mp3 = None
        SESSION.said_handoff = False
        SESSION.patched_user = False

        return kpi


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    UI_CLIENTS.add(ws)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        UI_CLIENTS.discard(ws)


@app.post("/api/start")
async def api_start(payload: StartPayload):
    async with SESSION_LOCK:
        if SESSION.active:
            return JSONResponse({"ok": False, "error": "Session already active"}, status_code=409)

        errors = _start_config_errors() + _start_payload_errors(payload)
        if errors:
            return JSONResponse({"ok": False, "errors": errors}, status_code=400)

        SESSION.target_number = payload.target_number
        SESSION.user_phone = payload.user_phone
        SESSION.goal_state = payload.goal_state
        SESSION.call_reason = payload.call_reason
        SESSION.ivr_system = payload.target_number
        SESSION.conference_name = f"phantom-{int(time.time())}"
        SESSION.business_call_sid = None
        SESSION.user_call_sid = None
        SESSION.handoff_mp3 = None
        SESSION.said_handoff = False
        SESSION.patched_user = False
        SESSION.started_at = time.time()
        SESSION.last_end_reason = ""

        agent.reset()
        state_machine.start(SESSION.ivr_system)

        target_number = SESSION.target_number
        twiml_url = f"{PUBLIC_BASE_URL}/twiml/outbound"
        SESSION.active = True

    try:
        sid = await asyncio.to_thread(telephony.start_outbound_call, target_number, twiml_url)
    except Exception as e:
        state_machine.fail(f"start_outbound_failed:{e}")
        async with SESSION_LOCK:
            SESSION.active = False
            SESSION.last_end_reason = f"start_failed:{e}"
        await ui_broadcast(f"Failed to start outbound call: {e}")
        return JSONResponse({"ok": False, "error": f"Outbound call failed: {e}"}, status_code=502)

    async with SESSION_LOCK:
        SESSION.business_call_sid = sid

    await ui_broadcast(f"Outbound call started to {target_number} (sid={sid})")
    return {"ok": True, "business_call_sid": sid, "conference": SESSION.conference_name}


@app.post("/api/stop")
async def api_stop():
    async with SESSION_LOCK:
        if not SESSION.active:
            return {"ok": True, "status": "no active session"}
        business_sid = SESSION.business_call_sid
        user_sid = SESSION.user_call_sid

    if business_sid:
        try:
            await asyncio.to_thread(telephony.hangup, business_sid)
        except Exception as e:
            await ui_broadcast(f"Failed to hang up business leg: {e}")

    if user_sid:
        try:
            await asyncio.to_thread(telephony.hangup, user_sid)
        except Exception as e:
            await ui_broadcast(f"Failed to hang up user leg: {e}")

    kpi = await _finalize_session("stop_api")
    if kpi:
        await ui_broadcast(
            "Session stopped. "
            f"Hold={_fmt_seconds(kpi.hold_seconds)}, saved={_fmt_seconds(kpi.saved_seconds)}"
        )
    return {"ok": True}


@app.get("/api/status")
async def api_status():
    return _build_status_payload()


@app.get("/api/metrics")
async def api_metrics():
    return {"ok": True, "metrics": metrics.summary()}


@app.get("/health")
async def health():
    errors = _start_config_errors()
    return {
        "ok": True,
        "start_ready": len(errors) == 0,
        "errors": errors,
    }


@app.get("/audio/handoff.mp3")
async def handoff_mp3():
    async with SESSION_LOCK:
        mp3 = SESSION.handoff_mp3
    if not mp3:
        return Response(status_code=404)
    return Response(mp3, media_type="audio/mpeg")


@app.post("/twiml/outbound")
async def twiml_outbound():
    if not WS_MEDIA_URL:
        return Response(
            "<Response><Say>Configuration error. Missing media stream URL.</Say></Response>",
            media_type="application/xml",
            status_code=500,
        )
    async with SESSION_LOCK:
        conference_name = SESSION.conference_name
    xml = build_twiml_outbound(ws_media_url=WS_MEDIA_URL, conference_name=conference_name)
    return Response(xml, media_type="application/xml")


@app.post("/twiml/join_user")
async def twiml_join_user():
    async with SESSION_LOCK:
        has_handoff = SESSION.handoff_mp3 is not None
        conference_name = SESSION.conference_name
    mp3_url = f"{PUBLIC_BASE_URL}/audio/handoff.mp3" if has_handoff else None
    xml = build_twiml_join_user(
        conference_name=conference_name,
        handoff_mp3_url=mp3_url,
    )
    return Response(xml, media_type="application/xml")


@app.websocket("/ws/media")
async def ws_media(ws: WebSocket):
    await ws.accept()
    await ui_broadcast("Media stream connected.")

    pcm_buf = bytearray()
    last_plan_time = 0.0
    last_logged_transcript = ""
    last_transcript_log_time = 0.0
    active_stream_sid: Optional[str] = None
    stream_bound = False

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "start":
                start_obj = msg.get("start", {}) or {}
                event_call_sid = start_obj.get("callSid")
                active_stream_sid = msg.get("streamSid") or start_obj.get("streamSid")

                async with SESSION_LOCK:
                    session_active = SESSION.active
                    business_sid = SESSION.business_call_sid

                stream_bound = bool(
                    session_active and event_call_sid and business_sid and event_call_sid == business_sid
                )

                if stream_bound:
                    await ui_broadcast(f"Twilio stream start (streamSid={active_stream_sid}).")
                else:
                    await ui_broadcast(
                        f"Ignoring stream start for sid={event_call_sid}; current sid={business_sid}"
                    )
                continue

            if event == "stop":
                await ui_broadcast("Twilio stream stop.")
                await _finalize_session("stream_stop")
                break

            if event != "media":
                continue

            async with SESSION_LOCK:
                session_active = SESSION.active
            if not session_active or not stream_bound:
                continue

            event_stream_sid = msg.get("streamSid") or msg.get("media", {}).get("streamSid")
            if active_stream_sid and event_stream_sid and event_stream_sid != active_stream_sid:
                continue

            payload_b64 = msg["media"]["payload"]
            pcm16 = twilio_ulaw_b64_to_pcm16(payload_b64)
            pcm_buf.extend(pcm16)

            if len(pcm_buf) < 3200:
                continue

            chunk = bytes(pcm_buf[:3200])
            del pcm_buf[:3200]

            frames_20ms = list(chunk_bytes(chunk, 320))
            energy = rms_energy(chunk)

            await asyncio.to_thread(agent.ingest_audio_and_transcribe, chunk)
            speech_ratio = await asyncio.to_thread(agent.get_speech_ratio, frames_20ms)
            classification = state_machine.apply_audio_observation(
                transcript=agent.mem.last_transcript,
                speech_ratio=speech_ratio,
                energy=energy,
            )

            if classification == "human":
                agent.mem.human_detected = True
            agent.mem.phase = classification

            transcript = (agent.mem.last_transcript or "").strip()
            now = time.time()
            if (
                transcript
                and transcript != last_logged_transcript
                and (now - last_transcript_log_time) >= 0.8
            ):
                last_logged_transcript = transcript
                last_transcript_log_time = now
                await ui_broadcast(f'IVR said: "{transcript}"')

            await ui_broadcast(
                "state="
                f"{state_machine.state} | phase={classification} | speech_ratio={speech_ratio:.2f} "
                f"| transcript='{agent.mem.last_transcript[:70]}'"
            )

            now = time.time()
            if now - last_plan_time < 2.0:
                continue
            last_plan_time = now

            async with SESSION_LOCK:
                goal_state = SESSION.goal_state
                call_reason = SESSION.call_reason
                ivr_system = SESSION.ivr_system
                said_handoff = SESSION.said_handoff
                patched_user = SESSION.patched_user

            obs = {
                "goal_state": goal_state,
                "call_reason": call_reason,
                "ivr_system": ivr_system,
                "audio_state": agent.mem.phase,
                "state_machine": state_machine.state,
                "partial_transcript": agent.mem.last_transcript,
                "speech_ratio": round(speech_ratio, 3),
                "energy": round(energy, 2),
                "pressed_digits": agent.mem.pressed_digits[-10:],
                "recent_actions": [
                    {"action": a.get("action"), "digit": a.get("digit"), "seconds": a.get("seconds")}
                    for a in agent.mem.recent_actions[-8:]
                ],
                "human_detected": agent.mem.human_detected,
                "said_handoff": said_handoff,
                "patched_user": patched_user,
            }

            try:
                action = await asyncio.to_thread(agent.plan, obs)
            except Exception as e:
                await ui_broadcast(f"Planner error; using WAIT fallback: {e}")
                action = {"action": "WAIT", "seconds": 2, "reason": "Planner error fallback"}

            agent.remember_action(action)
            state_machine.on_action(action.get("action", ""))
            await ui_broadcast(f"PLAN -> {action}")

            a = action.get("action")

            if a == "PRESS_DTMF":
                async with SESSION_LOCK:
                    business_sid = SESSION.business_call_sid
                if business_sid:
                    try:
                        await asyncio.to_thread(telephony.send_dtmf, business_sid, action["digit"])
                        await ui_broadcast(f"DTMF sent: {action['digit']}")
                    except Exception as e:
                        await ui_broadcast(f"Failed to send DTMF: {e}")

            elif a == "SAY_HANDOFF":
                async with SESSION_LOCK:
                    should_generate_handoff = (
                        not SESSION.handoff_mp3 and bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)
                    )
                    call_reason = SESSION.call_reason
                if should_generate_handoff:
                    handoff_text = (
                        "Hi, this is an automated assistant calling on behalf of the user. "
                        f"The user wants: {call_reason}. "
                        "I am connecting them now."
                    )
                    await ui_broadcast("Generating ElevenLabs handoff audio...")
                    try:
                        mp3 = await asyncio.to_thread(
                            elevenlabs_tts_mp3,
                            ELEVENLABS_API_KEY,
                            ELEVENLABS_VOICE_ID,
                            handoff_text,
                        )
                        async with SESSION_LOCK:
                            SESSION.handoff_mp3 = mp3
                        await ui_broadcast("Handoff audio ready.")
                    except Exception as e:
                        await ui_broadcast(f"ElevenLabs error: {e}")
                        async with SESSION_LOCK:
                            SESSION.handoff_mp3 = None

                async with SESSION_LOCK:
                    SESSION.said_handoff = True

            elif a == "PATCH_USER_IN":
                async with SESSION_LOCK:
                    can_patch = (not SESSION.patched_user and bool(SESSION.user_phone))
                    user_phone = SESSION.user_phone
                if can_patch:
                    twiml_url = f"{PUBLIC_BASE_URL}/twiml/join_user"
                    try:
                        sid = await asyncio.to_thread(
                            telephony.call_user_and_join,
                            user_phone,
                            twiml_url,
                        )
                        async with SESSION_LOCK:
                            SESSION.user_call_sid = sid
                            SESSION.patched_user = True
                        state_machine.on_user_bridged()
                        await ui_broadcast(
                            f"Calling user {user_phone} to patch in (sid={sid})"
                        )
                    except Exception as e:
                        await ui_broadcast(f"Patch-in call failed: {e}")

            elif a == "WAIT":
                pass

            elif a == "HANGUP":
                await ui_broadcast("Agent requested hangup.")
                async with SESSION_LOCK:
                    business_sid = SESSION.business_call_sid
                    user_sid = SESSION.user_call_sid
                if business_sid:
                    try:
                        await asyncio.to_thread(telephony.hangup, business_sid)
                    except Exception as e:
                        await ui_broadcast(f"Failed to hang up business leg: {e}")
                if user_sid:
                    try:
                        await asyncio.to_thread(telephony.hangup, user_sid)
                    except Exception as e:
                        await ui_broadcast(f"Failed to hang up user leg: {e}")

                kpi = await _finalize_session("agent_hangup")
                if kpi:
                    await ui_broadcast(
                        "Session complete. "
                        f"Hold={_fmt_seconds(kpi.hold_seconds)}, saved={_fmt_seconds(kpi.saved_seconds)}"
                    )
                break

    except WebSocketDisconnect:
        await ui_broadcast("Media WS disconnected.")
        await _finalize_session("ws_disconnect")
    except Exception as e:
        await ui_broadcast(f"Media WS ERROR: {e}")
        state_machine.fail(f"media_ws_error:{e}")
        await _finalize_session("ws_error")
    finally:
        try:
            await ws.close()
        except Exception:
            pass

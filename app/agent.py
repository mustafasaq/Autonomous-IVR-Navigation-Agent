import json
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import numpy as np
import webrtcvad
from vosk import Model, KaldiRecognizer
from google import genai


@dataclass
class AgentMemory:
    pressed_digits: List[str] = field(default_factory=list)
    recent_actions: List[Dict[str, Any]] = field(default_factory=list)
    human_detected: bool = False
    last_transcript: str = ""
    phase: str = "listening"


class IVRAgent:
    DIGIT_WORDS = {
        "0": ["zero", "oh"],
        "1": ["one"],
        "2": ["two", "to", "too"],
        "3": ["three"],
        "4": ["four", "for"],
        "5": ["five"],
        "6": ["six"],
        "7": ["seven"],
        "8": ["eight", "ate"],
        "9": ["nine"],
        "*": ["star", "asterisk"],
        "#": ["pound", "hash"],
    }

    def __init__(self, gemini_key: str, vosk_model_path: str):
        self.client = genai.Client(api_key=gemini_key) if gemini_key else None
        self.vad = webrtcvad.Vad(2)
        self.model = Model(vosk_model_path)
        self.in_sample_rate = 8000
        self.vosk_sample_rate = 16000
        self.rec = KaldiRecognizer(self.model, self.vosk_sample_rate)
        self.rec.SetWords(False)
        self.mem = AgentMemory()
        self.speech_flags: List[bool] = []
        self.last_speech_ratio: float = 0.0
        self.hold_energy_threshold = 450.0

    def reset(self):
        self.mem = AgentMemory()
        self.speech_flags = []
        self.last_speech_ratio = 0.0
        self.rec = KaldiRecognizer(self.model, self.vosk_sample_rate)
        self.rec.SetWords(False)

    def _resample_8k_to_16k(self, pcm16_8k: bytes) -> bytes:
        if not pcm16_8k:
            return b""
        samples = np.frombuffer(pcm16_8k, dtype=np.int16)
        if samples.size == 0:
            return b""
        upsampled = np.repeat(samples, 2)
        return upsampled.astype(np.int16).tobytes()

    def ingest_audio_and_transcribe(self, pcm16_8k: bytes) -> Optional[str]:
        pcm16_16k = self._resample_8k_to_16k(pcm16_8k)
        _ = self.rec.AcceptWaveform(pcm16_16k)
        partial_json = self.rec.PartialResult()

        try:
            obj = json.loads(partial_json)
            text = (obj.get("partial") or "").strip()
        except Exception:
            text = ""

        if text:
            self.mem.last_transcript = text
            return text
        return None

    def _vad_is_speech_20ms(self, frame_20ms_pcm16_8k: bytes) -> bool:
        if len(frame_20ms_pcm16_8k) != 320:
            return False
        return self.vad.is_speech(frame_20ms_pcm16_8k, self.in_sample_rate)

    def update_phase(self, frames_20ms: List[bytes], energy: float) -> str:
        speech_ratio = self.get_speech_ratio(frames_20ms)
        t = (self.mem.last_transcript or "").lower()

        if speech_ratio > 0.55:
            self.mem.phase = "human"
            self.mem.human_detected = True
        elif energy > self.hold_energy_threshold and speech_ratio < 0.15:
            self.mem.phase = "hold"
        else:
            if any(k in t for k in ["press", "option", "for", "representative", "agent"]):
                self.mem.phase = "menu"
            else:
                self.mem.phase = "listening"

        return self.mem.phase

    def get_speech_ratio(self, frames_20ms: List[bytes]) -> float:
        votes = [self._vad_is_speech_20ms(f) for f in frames_20ms]
        self.speech_flags.extend(votes)
        self.speech_flags = self.speech_flags[-50:]  # ~1s history
        self.last_speech_ratio = sum(self.speech_flags) / max(1, len(self.speech_flags))
        return self.last_speech_ratio

    def _build_prompt(self, obs: Dict[str, Any]) -> str:
        return f"""SYSTEM:
You are an autonomous IVR navigation agent.

Goal: reach the user's GOAL_STATE by listening to the phone audio and selecting ONE tool action each step.

Hard rules:
- Only press digits explicitly stated by the IVR/menu transcript.
- If uncertain, WAIT and gather more audio.
- Avoid loops: do not press the same digit repeatedly without new evidence.
- If a HUMAN is detected: first SAY_HANDOFF, then PATCH_USER_IN.
- Never impersonate a human. If speaking, identify as an automated assistant.

Output must be exactly ONE JSON object of these forms:
{{"action":"PRESS_DTMF","digit":"0-9|*|#","reason":"..."}}
{{"action":"WAIT","seconds":1-10,"reason":"..."}}
{{"action":"SAY_HANDOFF","reason":"..."}}
{{"action":"PATCH_USER_IN","reason":"..."}}
{{"action":"HANGUP","reason":"..."}}

OBSERVATION:
{json.dumps(obs, ensure_ascii=False)}
"""

    def plan(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client:
            return self._sanitize_action(self._fallback_action(obs), obs)

        prompt = self._build_prompt(obs)

        try:
            resp = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = (resp.text or "").strip()
        except Exception:
            return self._sanitize_action(self._fallback_action(obs), obs)

        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1].replace("json", "", 1).strip()

        try:
            action = json.loads(text)
        except Exception:
            action = self._fallback_action(obs)

        return self._sanitize_action(action, obs)

    def _fallback_action(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        if obs.get("human_detected") and not obs.get("said_handoff"):
            return {"action": "SAY_HANDOFF", "reason": "Human detected by VAD fallback"}
        if obs.get("human_detected") and obs.get("said_handoff") and not obs.get("patched_user"):
            return {"action": "PATCH_USER_IN", "reason": "Patch user after handoff fallback"}
        return {"action": "WAIT", "seconds": 2, "reason": "Planner unavailable fallback"}

    def _sanitize_action(self, action: Dict[str, Any], obs: Dict[str, Any]) -> Dict[str, Any]:
        a = action.get("action")

        if a == "PRESS_DTMF":
            digit = str(action.get("digit", "")).strip()

            if digit not in list("0123456789") + ["*", "#"]:
                return {"action": "WAIT", "seconds": 2, "reason": "Invalid digit; fallback"}

            if self.mem.pressed_digits and self.mem.pressed_digits[-1] == digit:
                return {"action": "WAIT", "seconds": 2, "reason": "Preventing repeated digit spam"}

            transcript = (obs.get("partial_transcript") or "").lower()
            if not self._has_digit_evidence(digit, transcript):
                return {"action": "WAIT", "seconds": 2, "reason": "No transcript evidence for digit"}

            return {"action": "PRESS_DTMF", "digit": digit, "reason": action.get("reason", "")}

        if a == "WAIT":
            sec = int(action.get("seconds", 2))
            sec = max(1, min(10, sec))
            return {"action": "WAIT", "seconds": sec, "reason": action.get("reason", "")}

        if a == "PATCH_USER_IN" and not obs.get("said_handoff"):
            return {"action": "WAIT", "seconds": 2, "reason": "Must say handoff before patching user in"}

        if a in ["SAY_HANDOFF", "PATCH_USER_IN", "HANGUP"]:
            return {"action": a, "reason": action.get("reason", "")}

        return {"action": "WAIT", "seconds": 2, "reason": "Unknown action; fallback"}

    def _has_digit_evidence(self, digit: str, transcript: str) -> bool:
        if digit in transcript:
            return True
        words = self.DIGIT_WORDS.get(digit, [])
        return any(w in transcript for w in words)

    def remember_action(self, action: Dict[str, Any]):
        entry = {"t": time.time(), **action}
        self.mem.recent_actions.append(entry)
        self.mem.recent_actions = self.mem.recent_actions[-20:]

        if action.get("action") == "PRESS_DTMF":
            self.mem.pressed_digits.append(action.get("digit"))
            self.mem.pressed_digits = self.mem.pressed_digits[-20:]

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


STATES = {
    "IDLE",
    "LISTENING",
    "MENU",
    "HOLD",
    "HUMAN_DETECTED",
    "HANDOFF_READY",
    "PATCHING_USER",
    "BRIDGED",
    "FINISHED",
    "ERROR",
}


@dataclass
class StateTransition:
    at: float
    from_state: str
    to_state: str
    reason: str


@dataclass
class CallStateMachine:
    state: str = "IDLE"
    ivr_system: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    hold_started_at: Optional[float] = None
    hold_seconds: float = 0.0
    transitions: List[StateTransition] = field(default_factory=list)
    last_classification: str = "listening"

    def start(self, ivr_system: str) -> None:
        self.state = "LISTENING"
        self.ivr_system = ivr_system or "unknown"
        self.started_at = time.time()
        self.finished_at = None
        self.hold_started_at = None
        self.hold_seconds = 0.0
        self.last_classification = "listening"
        self.transitions = []
        self.transitions.append(
            StateTransition(
                at=self.started_at,
                from_state="IDLE",
                to_state="LISTENING",
                reason="session_started",
            )
        )

    def finish(self, reason: str) -> None:
        if self.state == "FINISHED":
            return
        self._close_hold_window()
        prev = self.state
        self.state = "FINISHED"
        self.finished_at = time.time()
        self.transitions.append(
            StateTransition(
                at=self.finished_at,
                from_state=prev,
                to_state="FINISHED",
                reason=reason,
            )
        )

    def fail(self, reason: str) -> None:
        prev = self.state
        self._close_hold_window()
        self.state = "ERROR"
        self.finished_at = time.time()
        self.transitions.append(
            StateTransition(
                at=self.finished_at,
                from_state=prev,
                to_state="ERROR",
                reason=reason,
            )
        )

    def apply_audio_observation(
        self,
        transcript: str,
        speech_ratio: float,
        energy: float,
    ) -> str:
        transcript_l = (transcript or "").lower()
        classification = self._classify(transcript_l, speech_ratio, energy)
        self.last_classification = classification

        if classification == "human":
            self._transition("HUMAN_DETECTED", "vad_speech_ratio_high")
            self._close_hold_window()
        elif classification == "hold":
            self._transition("HOLD", "high_energy_low_speech_ratio")
            if self.hold_started_at is None:
                self.hold_started_at = time.time()
        elif classification == "menu":
            self._transition("MENU", "menu_keywords_from_asr")
            self._close_hold_window()
        else:
            if self.state in {"LISTENING", "MENU", "HOLD"}:
                self._transition("LISTENING", "default_listening")
            self._close_hold_window()

        return classification

    def on_action(self, action: str) -> None:
        if action == "SAY_HANDOFF":
            self._transition("HANDOFF_READY", "agent_announced_handoff")
            self._close_hold_window()
            return
        if action == "PATCH_USER_IN":
            self._transition("PATCHING_USER", "agent_started_user_patch")
            self._close_hold_window()
            return

    def on_user_bridged(self) -> None:
        self._transition("BRIDGED", "twilio_conference_bridged")
        self._close_hold_window()

    def _classify(self, transcript: str, speech_ratio: float, energy: float) -> str:
        if speech_ratio > 0.55:
            return "human"
        if energy > 450.0 and speech_ratio < 0.15:
            return "hold"
        if any(k in transcript for k in ["press", "option", "representative", "agent", "for"]):
            return "menu"
        return "listening"

    def _transition(self, to_state: str, reason: str) -> None:
        if to_state not in STATES:
            return
        if self.state == to_state:
            return
        now = time.time()
        self.transitions.append(
            StateTransition(
                at=now,
                from_state=self.state,
                to_state=to_state,
                reason=reason,
            )
        )
        self.state = to_state

    def _close_hold_window(self) -> None:
        if self.hold_started_at is None:
            return
        self.hold_seconds += max(0.0, time.time() - self.hold_started_at)
        self.hold_started_at = None

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        active_hold = 0.0
        if self.hold_started_at is not None:
            active_hold = max(0.0, now - self.hold_started_at)
        runtime = 0.0
        if self.started_at is not None:
            runtime = max(0.0, (self.finished_at or now) - self.started_at)
        return {
            "state": self.state,
            "ivr_system": self.ivr_system,
            "last_classification": self.last_classification,
            "hold_seconds": round(self.hold_seconds + active_hold, 2),
            "runtime_seconds": round(runtime, 2),
            "transition_count": len(self.transitions),
            "last_transition": (
                {
                    "at": self.transitions[-1].at,
                    "from": self.transitions[-1].from_state,
                    "to": self.transitions[-1].to_state,
                    "reason": self.transitions[-1].reason,
                }
                if self.transitions
                else None
            ),
        }

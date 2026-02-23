from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Set


@dataclass
class SessionKPI:
    ivr_system: str
    started_at: float
    ended_at: float
    runtime_seconds: float
    hold_seconds: float
    digits_pressed: int
    actions_count: int
    patched_user: bool
    human_detected: bool
    ended_reason: str

    @property
    def saved_seconds(self) -> float:
        if not self.patched_user:
            return 0.0
        return self.hold_seconds


@dataclass
class MetricsStore:
    sessions: List[SessionKPI] = field(default_factory=list)
    unique_systems: Set[str] = field(default_factory=set)

    def record(self, kpi: SessionKPI) -> None:
        self.sessions.append(kpi)
        self.unique_systems.add(kpi.ivr_system or "unknown")
        self.sessions = self.sessions[-500:]

    def summary(self) -> Dict[str, Any]:
        total = len(self.sessions)
        completed = [s for s in self.sessions if s.ended_reason in {"stop_api", "agent_hangup", "stream_stop"}]
        autonomous = [s for s in self.sessions if s.digits_pressed > 0]
        with_patch = [s for s in self.sessions if s.patched_user]

        avg_hold = 0.0
        avg_saved = 0.0
        if total:
            avg_hold = sum(s.hold_seconds for s in self.sessions) / total
            avg_saved = sum(s.saved_seconds for s in self.sessions) / total

        systems_covered = len(self.unique_systems)
        goal_met = avg_saved >= (15 * 60) and systems_covered >= 10

        return {
            "sessions_total": total,
            "sessions_completed": len(completed),
            "autonomous_sessions": len(autonomous),
            "systems_covered": systems_covered,
            "avg_hold_seconds": round(avg_hold, 2),
            "avg_saved_seconds": round(avg_saved, 2),
            "avg_saved_minutes": round(avg_saved / 60.0, 2),
            "patched_sessions": len(with_patch),
            "target_avg_saved_minutes": 15,
            "target_systems_covered": 10,
            "goal_15m_10systems_met": goal_met,
            "last_updated_at": time.time(),
            "latest_session": (
                {
                    "ivr_system": self.sessions[-1].ivr_system,
                    "runtime_seconds": round(self.sessions[-1].runtime_seconds, 2),
                    "hold_seconds": round(self.sessions[-1].hold_seconds, 2),
                    "saved_seconds": round(self.sessions[-1].saved_seconds, 2),
                    "digits_pressed": self.sessions[-1].digits_pressed,
                    "ended_reason": self.sessions[-1].ended_reason,
                }
                if self.sessions
                else None
            ),
        }

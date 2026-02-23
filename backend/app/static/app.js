const logList = document.getElementById("logList");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");

const metricState = document.getElementById("metricState");
const metricPhase = document.getElementById("metricPhase");
const metricSystems = document.getElementById("metricSystems");
const metricSaved = document.getElementById("metricSaved");
const metricTarget = document.getElementById("metricTarget");
const liveDot = document.getElementById("liveDot");
const liveState = document.getElementById("liveState");

function formatState(raw) {
  return String(raw || "IDLE")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toUpperCase();
}

function setMetricState(raw) {
  const normalized = String(raw || "IDLE")
    .replace(/\s+/g, "_")
    .toUpperCase();

  metricState.textContent = formatState(normalized);
  metricState.className = "metric-value";

  if (normalized === "LISTENING") {
    metricState.classList.add("state-listening");
  } else if (normalized === "FINISHED") {
    metricState.classList.add("state-finished");
  }
}

function formatPhase(raw) {
  return String(raw || "listening")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function classifyLog(msg) {
  const m = msg.toLowerCase();
  if (m.includes("error") || m.includes("failed")) return "error";
  if (m.startsWith("plan ->")) return "plan";
  if (m.startsWith("state=")) return "state";
  if (m.startsWith("ivr said:")) return "ivr";
  if (m.includes("dtmf") || m.includes("patch") || m.includes("hangup")) return "action";
  return "info";
}

function normalizeLogBody(body, kind) {
  if (kind !== "state") {
    return body;
  }

  const state = body.match(/state=([^|\s]+)/)?.[1];
  const phase = body.match(/phase=([^|\s]+)/)?.[1];
  const ratio = body.match(/speech_ratio=([^|\s]+)/)?.[1];

  const chunks = [];
  if (state) chunks.push(`State: ${formatState(state)}`);
  if (phase) chunks.push(formatPhase(phase));
  if (ratio) chunks.push(`Speech: ${ratio}`);

  return chunks.length ? chunks.join(" | ") : body;
}

function appendLog(raw) {
  const line = String(raw || "").trim();
  if (!line) return;

  const match = line.match(/^(\d{2}:\d{2}:\d{2})\s-\s(.*)$/);
  const ts = match ? match[1] : "--:--:--";
  const body = match ? match[2] : line;

  const kind = classifyLog(body);
  const text = normalizeLogBody(body, kind);

  const item = document.createElement("li");
  item.className = `log-${kind}`;

  const tsEl = document.createElement("span");
  tsEl.className = "log-ts";
  tsEl.textContent = ts;

  const msgEl = document.createElement("span");
  msgEl.className = "log-msg";
  msgEl.textContent = text;

  item.append(tsEl, msgEl);
  logList.prepend(item);

  while (logList.children.length > 200) {
    logList.removeChild(logList.lastChild);
  }
}

function setLive(active, label) {
  liveState.textContent = label;
  if (active) {
    liveDot.classList.add("live");
  } else {
    liveDot.classList.remove("live");
  }
}

function updateFromStatus(status) {
  if (!status || !status.session || !status.state_machine || !status.metrics) {
    return;
  }

  const active = Boolean(status.session.active);
  const state = formatState(status.state_machine.state);
  const phase = formatPhase(status.state_machine.last_classification);

  setMetricState(state);
  metricPhase.textContent = phase;
  metricSystems.textContent = String(status.metrics.systems_covered ?? 0);
  metricSaved.textContent = `${Number(status.metrics.avg_saved_minutes || 0).toFixed(2)}m`;
  metricTarget.textContent = status.metrics.goal_15m_10systems_met
    ? "Target met: 15m avg / 10 systems"
    : "Target pending: 15m avg / 10 systems";

  setLive(active, active ? "Calling" : "Idle");
}

async function pollStatus() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) return;
    const j = await r.json();
    updateFromStatus(j);
  } catch (e) {
    appendLog(`STATUS_ERR: ${e}`);
  }
}

const wsProto = location.protocol === "https:" ? "wss://" : "ws://";
const uiWS = new WebSocket(wsProto + location.host + "/ws/ui");

uiWS.onopen = () => appendLog("UI stream connected.");
uiWS.onclose = () => appendLog("UI stream disconnected.");
uiWS.onerror = () => appendLog("UI stream error.");
uiWS.onmessage = (e) => {
  const line = String(e.data || "");
  appendLog(line);

  if (line.includes("state=")) {
    const stateMatch = line.match(/state=([^|\s]+)/);
    const phaseMatch = line.match(/phase=([^|\s]+)/);
    if (stateMatch) setMetricState(stateMatch[1]);
    if (phaseMatch) metricPhase.textContent = formatPhase(phaseMatch[1]);
  }
};

startBtn.onclick = async () => {
  startBtn.disabled = true;
  startBtn.textContent = "Starting...";

  const payload = {
    target_number: document.getElementById("target").value.trim(),
    user_phone: document.getElementById("userPhone").value.trim(),
    goal_state: document.getElementById("goal").value.trim(),
    call_reason: document.getElementById("reason").value.trim(),
  };

  try {
    const r = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const j = await r.json();
    appendLog("START: " + JSON.stringify(j));
    if (r.ok && j.ok) {
      setLive(true, "Calling");
    }
  } catch (e) {
    appendLog(`START_ERR: ${e}`);
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = "Start Call";
    pollStatus();
  }
};

stopBtn.onclick = async () => {
  stopBtn.disabled = true;
  stopBtn.textContent = "Ending...";

  try {
    const r = await fetch("/api/stop", { method: "POST" });
    const j = await r.json();
    appendLog("STOP: " + JSON.stringify(j));
  } catch (e) {
    appendLog(`STOP_ERR: ${e}`);
  } finally {
    stopBtn.disabled = false;
    stopBtn.textContent = "End Call";
    pollStatus();
  }
};

pollStatus();
setInterval(pollStatus, 2500);

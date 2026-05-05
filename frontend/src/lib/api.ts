const API_BASE = "/api";

export async function createSession(topic: string, maxRounds: number = 3) {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, max_rounds: maxRounds }),
  });
  if (!res.ok) throw new Error(`Failed to create session: ${res.statusText}`);
  return res.json();
}

export async function getSession(sessionId: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
  if (!res.ok) throw new Error(`Session not found: ${res.statusText}`);
  return res.json();
}

export async function listSessions() {
  const res = await fetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error(`Failed to list sessions: ${res.statusText}`);
  return res.json();
}

export async function clarifyTopic(sessionId: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/clarify`, { method: "POST" });
  if (!res.ok) throw new Error(`Clarify failed: ${res.statusText}`);
  return res.json();
}

export async function refineTopic(sessionId: string, answer: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok) throw new Error(`Refine failed: ${res.statusText}`);
  return res.json();
}

export async function suggestAngles(sessionId: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/suggest-angles`, { method: "POST" });
  if (!res.ok) throw new Error(`Suggest angles failed: ${res.statusText}`);
  return res.json();
}

export async function startDiscussion(
  sessionId: string,
  angleIds: string[],
  customAngles?: { name: string; description: string }[]
) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ angle_ids: angleIds, custom_angles: customAngles || null }),
  });
  if (!res.ok) throw new Error(`Start discussion failed: ${res.statusText}`);
  return res.json();
}

export async function getMinutes(sessionId: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/minutes`);
  if (!res.ok) throw new Error(`Minutes not available: ${res.statusText}`);
  return res.json();
}

export async function getMessages(sessionId: string) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`Messages not available: ${res.statusText}`);
  return res.json();
}

export function getStreamUrl(sessionId: string) {
  return `${API_BASE}/sessions/${sessionId}/stream`;
}

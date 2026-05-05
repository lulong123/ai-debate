const API_BASE = "/api";

// Response types matching backend API contracts
export interface CreateSessionResponse {
  session_id: string;
  status: string;
}

export interface SessionResponse {
  session_id: string;
  topic: string;
  refined_topic: string | null;
  status: string;
  current_round: number;
  max_rounds: number;
  created_at: string | null;
  completed_at: string | null;
}

export interface SessionListItem {
  session_id: string;
  topic: string;
  status: string;
  current_round: number;
  created_at: string | null;
}

export interface ClarifyResponse {
  valid: boolean;
  reason: string;
  question: string;
  suggestion: string;
}

export interface AngleSuggestion {
  id: string;
  name: string;
  description: string;
}

export interface SuggestAnglesResponse {
  session_id: string;
  angles: AngleSuggestion[];
}

export interface MessageResponse {
  id: string;
  role: string;
  agent_name: string | null;
  angle_id: string | null;
  round_number: number | null;
  content: string;
  scores: Record<string, unknown> | null;
  created_at: string | null;
}

export interface MinutesResponse {
  session_id: string;
  minutes: {
    core_conclusion: string;
    standpoints: Array<{ angle: string; main_points: string[]; position: string }>;
    disagreements: string[];
    actionable_items: string[];
    summary: string;
  } | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(res.statusText);
  return res.json() as Promise<T>;
}

export async function createSession(topic: string, maxRounds: number = 3): Promise<CreateSessionResponse> {
  return request<CreateSessionResponse>(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, max_rounds: maxRounds }),
  });
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  return request<SessionResponse>(`${API_BASE}/sessions/${sessionId}`);
}

export async function listSessions(): Promise<SessionListItem[]> {
  return request<SessionListItem[]>(`${API_BASE}/sessions`);
}

export async function clarifyTopic(sessionId: string): Promise<ClarifyResponse> {
  return request<ClarifyResponse>(`${API_BASE}/sessions/${sessionId}/clarify`, { method: "POST" });
}

export async function refineTopic(sessionId: string, answer: string): Promise<{ session_id: string; refined_topic: string }> {
  return request(`${API_BASE}/sessions/${sessionId}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
}

export async function suggestAngles(sessionId: string): Promise<SuggestAnglesResponse> {
  return request<SuggestAnglesResponse>(`${API_BASE}/sessions/${sessionId}/suggest-angles`, { method: "POST" });
}

export async function startDiscussion(
  sessionId: string,
  angleIds: string[],
  customAngles?: { name: string; description: string }[]
): Promise<{ session_id: string; status: string }> {
  return request(`${API_BASE}/sessions/${sessionId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ angle_ids: angleIds, custom_angles: customAngles || null }),
  });
}

export async function getMinutes(sessionId: string): Promise<MinutesResponse> {
  return request<MinutesResponse>(`${API_BASE}/sessions/${sessionId}/minutes`);
}

export async function getMessages(sessionId: string): Promise<MessageResponse[]> {
  return request<MessageResponse[]>(`${API_BASE}/sessions/${sessionId}/messages`);
}

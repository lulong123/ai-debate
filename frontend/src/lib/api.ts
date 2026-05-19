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
  max_rounds: number;
  created_at: string | null;
  completed_at: string | null;
  winner: string;
}

export interface ClarifyResponse {
  valid: boolean;
  reason: string;
  question: string;
  suggestion: string;
}

export interface PositionSuggestion {
  id: string;
  name: string;
  description: string;
}

export interface SuggestPositionsResponse {
  session_id: string;
  positions: PositionSuggestion[];
  data_clerk_recommended: boolean;
  data_clerk_reason: string;
  preliminary_data: Array<{ title: string; snippet: string; url: string; publish_date: string }> | null;
}

export interface MessageResponse {
  id: string;
  role: string;
  agent_name: string | null;
  position_id: string | null;
  round_number: number | null;
  content: string;
  scores: Record<string, unknown> | null;
  created_at: string | null;
}

export interface MinutesResponse {
  session_id: string;
  minutes: {
    core_conclusion: string;
    position_arguments: Array<{
      position: string;
      main_points: string[];
      defense: string;
    }>;
    key_clashes: string[];
    verdict: {
      winner: string;
      rationale: string;
      score_summary: string;
    };
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

export async function listSessions(params?: { status?: string; search?: string; limit?: number; offset?: number }): Promise<SessionListItem[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return request<SessionListItem[]>(`${API_BASE}/sessions${query ? `?${query}` : ""}`);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(res.statusText);
}

export async function updateSession(sessionId: string, topic: string): Promise<{ session_id: string; topic: string }> {
  return request(`${API_BASE}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic }),
  });
}

export async function clarifyTopic(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/clarify`, { method: "POST" });
  if (!res.ok) throw new Error(res.statusText);
  // 202 — result comes via SSE
}

export async function refineTopic(sessionId: string, answer: string): Promise<{ session_id: string; refined_topic: string }> {
  return request(`${API_BASE}/sessions/${sessionId}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
}

export async function suggestPositions(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/suggest-positions`, { method: "POST" });
  if (!res.ok) throw new Error(res.statusText);
  // 202 — result comes via SSE
}

export async function startDiscussion(
  sessionId: string,
  positionIds: string[],
  customPositions?: { name: string; description: string }[],
  enableDataClerk?: boolean
): Promise<{ session_id: string; status: string }> {
  return request(`${API_BASE}/sessions/${sessionId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      position_ids: positionIds,
      custom_positions: customPositions || null,
      enable_data_clerk: enableDataClerk ?? false,
    }),
  });
}

export async function getMinutes(sessionId: string): Promise<MinutesResponse> {
  return request<MinutesResponse>(`${API_BASE}/sessions/${sessionId}/minutes`);
}

export async function getMessages(sessionId: string): Promise<MessageResponse[]> {
  return request<MessageResponse[]>(`${API_BASE}/sessions/${sessionId}/messages`);
}

export interface DataPoolItem {
  id: string;
  citation_num: number;
  source: string;
  title: string;
  snippet: string;
  url: string;
  publish_date: string;
  key_facts: string | null;
  round_number: number | null;
  created_at: string | null;
}

export interface PositionItem {
  id: string;
  name: string;
  description: string;
  is_custom: boolean;
}

export async function getDataPool(sessionId: string): Promise<DataPoolItem[]> {
  return request<DataPoolItem[]>(`${API_BASE}/sessions/${sessionId}/data-pool`);
}

export async function getPositions(sessionId: string): Promise<PositionItem[]> {
  return request<PositionItem[]>(`${API_BASE}/sessions/${sessionId}/positions`);
}

export async function addUserData(
  sessionId: string,
  title: string,
  content: string,
  url?: string
): Promise<{ id: string; status: string }> {
  return request(`${API_BASE}/sessions/${sessionId}/data-pool`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, content, url: url || "" }),
  });
}

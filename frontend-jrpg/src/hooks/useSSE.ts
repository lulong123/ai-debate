import { useCallback, useEffect, useRef, useState } from "react";

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export function useSSE(sessionId: string | null) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string | null>(null);

  const connect = useCallback(() => {
    if (!sessionId) return;

    esRef.current?.close();

    let url = `/api/sessions/${sessionId}/stream`;
    const lastId = lastEventIdRef.current;
    if (lastId) {
      url += `?last_event_id=${encodeURIComponent(lastId)}`;
    }

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => {
      setConnected(false);
      es.close();
      setTimeout(() => {
        if (esRef.current === es) connect();
      }, 3000);
    };

    const handleEvent = (e: MessageEvent) => {
      if (!e.data) return;
      try {
        const event = JSON.parse(e.data) as SSEEvent;
        setEvents((prev) => [...prev, event]);
        if (e.lastEventId) {
          lastEventIdRef.current = e.lastEventId;
        }
      } catch {
        // Ignore non-JSON
      }
    };

    es.onmessage = handleEvent;

    const eventTypes = [
      "discussion_start", "round_start", "agent_message_start",
      "agent_message_chunk", "agent_message_complete",
      "agent_thinking",
      "score_update", "moderator_guidance", "round_complete",
      "discussion_end", "error", "data_fetch_start", "data_fetch_complete",
      "user_data_added",
      "clarify_result", "positions_result",
    ];
    for (const type of eventTypes) {
      es.addEventListener(type, handleEvent);
    }
  }, [sessionId]);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [connect]);

  const clearEvents = useCallback(() => {
    setEvents([]);
    lastEventIdRef.current = null;
  }, []);

  return { events, connected, clearEvents };
}

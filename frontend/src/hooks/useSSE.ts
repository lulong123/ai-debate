import { useCallback, useEffect, useRef, useState } from "react";

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export function useSSE(sessionId: string | null) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    if (!sessionId) return;

    // Close existing connection
    esRef.current?.close();

    const url = `/api/sessions/${sessionId}/stream`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => {
      setConnected(false);
      es.close();
      // Auto-reconnect after 3s
      setTimeout(() => {
        if (esRef.current === es) connect();
      }, 3000);
    };

    es.onmessage = (e) => {
      if (e.data) {
        try {
          const event = JSON.parse(e.data) as SSEEvent;
          setEvents((prev) => [...prev, event]);
        } catch {
          // Ignore non-JSON messages
        }
      }
    };

    // Also listen for typed events
    const eventTypes = [
      "discussion_start", "round_start", "agent_message_start",
      "agent_message_chunk", "agent_message_complete",
      "score_update", "moderator_guidance", "round_complete",
      "discussion_end", "error",
    ];
    for (const type of eventTypes) {
      es.addEventListener(type, (e: MessageEvent) => {
        if (e.data) {
          try {
            const event = JSON.parse(e.data) as SSEEvent;
            setEvents((prev) => [...prev, event]);
          } catch {
            // Ignore
          }
        }
      });
    }
  }, [sessionId]);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [connect]);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { events, connected, clearEvents };
}

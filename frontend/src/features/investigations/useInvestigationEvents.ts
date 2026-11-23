import { useCallback, useEffect, useRef, useState } from "react";

import type { InvestigationEvent } from "@/lib/types";

/** Event names the backend emits (`ai_sre.services.investigations`). */
export const EVENT_TYPES = [
  "status",
  "node_started",
  "node_completed",
  "scope",
  "plan",
  "tool_call",
  "evidence_analyzed",
  "hypotheses",
  "decision",
  "root_cause",
  "proposals",
  "approval_required",
  "approval_decision",
  "cancel_requested",
  "verification",
  "report_ready",
  "error",
] as const;

export type ConnectionState = "connecting" | "open" | "reconnecting" | "ended" | "failed";

export interface InvestigationStream {
  events: InvestigationEvent[];
  connection: ConnectionState;
  /** Re-open the stream after it ended or failed, resuming after the last event seen. */
  reconnect: () => void;
}

interface Tagged<T> {
  key: string;
  value: T;
}

/**
 * Subscribe to an investigation's server-sent events.
 *
 * The browser's EventSource reconnects on its own after network drops and sends
 * `Last-Event-ID`, so the server replays only what was missed; events are also de-duplicated
 * by id. The server closes the stream with an `end` event when the run stops (finished or
 * waiting for approval); call `reconnect` when it is resumed.
 */
export function useInvestigationEvents(
  id: string,
  onEvent?: (event: InvestigationEvent) => void,
): InvestigationStream {
  const [generation, setGeneration] = useState(0);
  // State is tagged with the investigation / connection it belongs to, so switching either
  // one yields fresh values without resetting state inside an effect.
  const [events, setEvents] = useState<Tagged<InvestigationEvent[]>>({ key: id, value: [] });
  const [connection, setConnection] = useState<Tagged<ConnectionState>>({
    key: "",
    value: "connecting",
  });
  const cursor = useRef<{ id: string; last: number; seen: Set<number> }>({
    id,
    last: 0,
    seen: new Set(),
  });
  const handler = useRef(onEvent);

  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  const connKey = `${id}#${String(generation)}`;

  useEffect(() => {
    if (cursor.current.id !== id) cursor.current = { id, last: 0, seen: new Set() };
    const mark = (value: ConnectionState) => {
      setConnection({ key: connKey, value });
    };
    if (typeof EventSource === "undefined") {
      queueMicrotask(() => {
        mark("failed");
      });
      return;
    }
    const after = cursor.current.last ? `?after=${String(cursor.current.last)}` : "";
    const source = new EventSource(
      `/api/v1/investigations/${encodeURIComponent(id)}/events${after}`,
    );

    const receive = (message: MessageEvent<string>) => {
      const c = cursor.current;
      const eventId = Number(message.lastEventId);
      if (Number.isFinite(eventId) && eventId > 0) {
        if (c.seen.has(eventId)) return;
        c.seen.add(eventId);
        c.last = Math.max(c.last, eventId);
      }
      let parsed: { type?: string; data?: Record<string, unknown>; at?: string };
      try {
        parsed = JSON.parse(message.data) as typeof parsed;
      } catch {
        return;
      }
      const event: InvestigationEvent = {
        id: eventId,
        type: parsed.type ?? message.type,
        data: parsed.data ?? {},
        at: parsed.at ?? new Date().toISOString(),
      };
      setEvents((prev) => ({
        key: id,
        value: prev.key === id ? [...prev.value, event] : [event],
      }));
      handler.current?.(event);
    };

    source.onopen = () => {
      mark("open");
    };
    source.onerror = () => {
      // CONNECTING: the browser is retrying by itself. CLOSED: it gave up (e.g. HTTP 404).
      mark(source.readyState === EventSource.CLOSED ? "failed" : "reconnecting");
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, receive);
    source.addEventListener("end", () => {
      source.close();
      mark("ended");
    });

    return () => {
      source.close();
    };
  }, [id, connKey]);

  const reconnect = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);

  return {
    events: events.key === id ? events.value : [],
    connection: connection.key === connKey ? connection.value : "connecting",
    reconnect,
  };
}

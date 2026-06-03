// Telemetry source hook — SSE push (Phase A: Redis -> /api/stream) with an
// automatic fallback to polling /api/telemetry if SSE/Redis is unavailable.
//
// Kept deliberately separate from rendering so the transport can change (Redis
// SSE today, WebSocket/MAVLink later) without touching the UI components.

import { useEffect, useRef, useState } from 'react';

export function useTelemetry(intervalMs = 250) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [connected, setConnected] = useState(false);
  const [transport, setTransport] = useState('connecting'); // 'sse' | 'poll'
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    let es = null;
    let pollTimer = null;

    const applyRecord = (json) => {
      if (!aliveRef.current) return;
      if (json && json.error) {
        setError(json.message || json.error);
        setConnected(false);
      } else {
        setError(null);
        setConnected(true);
        setData(json);
      }
    };

    // ---- polling fallback ----
    const startPolling = () => {
      if (pollTimer) return;
      setTransport('poll');
      const poll = async () => {
        try {
          const res = await fetch('/api/telemetry', { cache: 'no-store' });
          applyRecord(await res.json());
        } catch (e) {
          if (aliveRef.current) { setError(String(e)); setConnected(false); }
        }
      };
      poll();
      pollTimer = setInterval(poll, intervalMs);
    };

    // ---- SSE (preferred) ----
    if (typeof window !== 'undefined' && 'EventSource' in window) {
      try {
        es = new EventSource('/api/stream');
        es.onopen = () => setTransport('sse');
        es.onmessage = (ev) => {
          try { applyRecord(JSON.parse(ev.data)); } catch {}
        };
        es.addEventListener('fatal', () => {       // route signalled Redis down
          es.close(); es = null; startPolling();
        });
        es.onerror = () => {                        // connection failed -> fall back
          es.close(); es = null; startPolling();
        };
      } catch {
        startPolling();
      }
    } else {
      startPolling();
    }

    return () => {
      aliveRef.current = false;
      if (es) es.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [intervalMs]);

  return { data, error, connected, transport };
}

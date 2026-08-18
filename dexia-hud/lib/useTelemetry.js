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
    let pollInterval = null;
    let reconnectInterval = null;
    let activeProbe = null;

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

    const stopPollingAndReconnecting = () => {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      if (reconnectInterval) {
        clearInterval(reconnectInterval);
        reconnectInterval = null;
      }
    };

    const startPolling = () => {
      if (!aliveRef.current) return;
      if (pollInterval) return; // Already polling

      setTransport('poll');
      const poll = async () => {
        try {
          const res = await fetch('/api/telemetry', { cache: 'no-store' });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          applyRecord(await res.json());
        } catch (e) {
          if (aliveRef.current) {
            setError(String(e));
            setConnected(false);
          }
        }
      };
      poll();
      pollInterval = setInterval(poll, intervalMs);

      if (!reconnectInterval) {
        reconnectInterval = setInterval(attemptSSEReconnect, 5000);
      }
    };

    const attemptSSEReconnect = () => {
      if (!aliveRef.current || activeProbe) return;
      if (typeof window === 'undefined' || !('EventSource' in window)) return;

      try {
        const probe = new EventSource('/api/stream');
        activeProbe = probe;

        probe.onmessage = (ev) => {
          try {
            const parsed = JSON.parse(ev.data);
            
            // Successfully received a valid message. Clear polling and promote this probe to active SSE.
            stopPollingAndReconnecting();
            if (es) {
              es.close();
            }
            es = probe;
            activeProbe = null;
            setTransport('sse');

            // Attach standard SSE listeners
            probe.onmessage = (e) => {
              try { applyRecord(JSON.parse(e.data)); } catch {}
            };
            probe.addEventListener('fatal', () => {
              if (es === probe) {
                probe.close();
                es = null;
                startPolling();
              }
            });
            probe.onerror = () => {
              if (es === probe) {
                probe.close();
                es = null;
                startPolling();
              }
            };

            applyRecord(parsed);
          } catch {}
        };

        probe.addEventListener('fatal', () => {
          probe.close();
          if (activeProbe === probe) activeProbe = null;
        });
        probe.onerror = () => {
          probe.close();
          if (activeProbe === probe) activeProbe = null;
        };
      } catch {
        if (activeProbe) {
          activeProbe.close();
          activeProbe = null;
        }
      }
    };

    // ---- Initial Connection: SSE-first ----
    if (typeof window !== 'undefined' && 'EventSource' in window) {
      try {
        const initialEs = new EventSource('/api/stream');
        es = initialEs;

        initialEs.onmessage = (ev) => {
          try {
            const parsed = JSON.parse(ev.data);
            setTransport('sse');
            applyRecord(parsed);
          } catch {}
        };

        initialEs.addEventListener('fatal', () => {
          if (es === initialEs) {
            initialEs.close();
            es = null;
            startPolling();
          }
        });

        initialEs.onerror = () => {
          if (es === initialEs) {
            initialEs.close();
            es = null;
            startPolling();
          }
        };
      } catch {
        startPolling();
      }
    } else {
      startPolling();
    }

    return () => {
      aliveRef.current = false;
      if (es) {
        es.close();
        es = null;
      }
      if (activeProbe) {
        activeProbe.close();
        activeProbe = null;
      }
      stopPollingAndReconnecting();
    };
  }, [intervalMs]);

  return { data, error, connected, transport };
}

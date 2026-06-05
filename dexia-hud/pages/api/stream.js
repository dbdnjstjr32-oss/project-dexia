// Server-Sent Events (SSE) telemetry push — Phase A consumer.
//
// Subscribes to the Redis pub/sub channel `dexia:telemetry` (published by the
// Python streamer's RedisSink) and pushes each tick to the browser over SSE.
// On connect it immediately replays the latest snapshot so a fresh client sees
// current state without waiting for the next tick. If Redis is unavailable the
// route fails fast (503) and the client falls back to polling /api/telemetry.

import { createClient } from 'redis';

const REDIS_URL = process.env.REDIS_URL || 'redis://127.0.0.1:6379';
const CHANNEL = 'dexia:telemetry';

export const config = { api: { bodyParser: false } };

export default async function handler(req, res) {
  // --- SSE headers ---
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');
  if (typeof res.flushHeaders === 'function') res.flushHeaders();

  let client, sub, heartbeat;
  const cleanup = () => {
    clearInterval(heartbeat);
    try { sub?.quit(); } catch {}
    try { client?.quit(); } catch {}
  };

  try {
    // Fail FAST when Redis is absent (e.g. streamer running with --no-redis):
    // no reconnect retries + short connect timeout, so connect() rejects in ~1s
    // and we emit `fatal` -> the client immediately falls back to polling
    // /api/telemetry (instead of hanging "connected" with no data = LINK DOWN).
    client = createClient({
      url: REDIS_URL,
      socket: { reconnectStrategy: false, connectTimeout: 1000 },
    });
    client.on('error', () => {}); // swallow; handled below
    await client.connect();

    // 1) replay latest snapshot immediately
    const latest = await client.get(`${CHANNEL}:latest`);
    if (latest) res.write(`data: ${latest}\n\n`);

    // 2) live subscribe
    sub = client.duplicate();
    sub.on('error', () => {});
    await sub.connect();
    await sub.subscribe(CHANNEL, (message) => {
      res.write(`data: ${message}\n\n`);
    });

    // keep-alive comment so proxies don't drop the idle connection
    heartbeat = setInterval(() => res.write(': ping\n\n'), 15000);
  } catch (err) {
    cleanup();
    // tell the client to stop and fall back to polling
    res.write(`event: fatal\ndata: ${JSON.stringify({ error: String(err) })}\n\n`);
    res.end();
    return;
  }

  req.on('close', cleanup);
}

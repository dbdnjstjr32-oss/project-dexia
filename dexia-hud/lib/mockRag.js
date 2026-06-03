// Mock LLM RAG — "AI Staff" Tactical Advisory.
//
// This stands in for a real Ollama/RAG backend. Instead of calling an LLM, it
// runs a deterministic rule-base over the current telemetry snapshot and emits
// natural-language tactical advisories. The rules are ordered by priority
// (interceptions first, then live threats, then mission status, then comms,
// then attrition), mirroring how a real retrieval-augmented staff officer would
// surface the most urgent intelligence first.

function roleOf(state, agentId) {
  const a = (state.agents || []).find((x) => x.id === agentId);
  return a ? a.role : agentId;
}

export function analyzeTelemetry(state) {
  const advisories = [];
  if (!state || !state.agents) {
    return [{ level: 'info', text: 'AI STAFF: Awaiting telemetry stream...' }];
  }

  const ev = state.events || {};
  const aa = state.aa;

  // 1) Anti-Air interceptions — highest priority.
  for (const a of state.agents) {
    if (a.lost && a.loss_reason === 'anti_air') {
      const group = a.kind === 'kami' ? 'Swarm Group B' : 'Swarm Group A';
      advisories.push({
        level: 'critical',
        text: `WARNING: ${a.role} intercepted by AA. Recommend re-routing ${group}.`,
      });
    } else if (a.lost && a.loss_reason === 'crash') {
      advisories.push({
        level: 'warning',
        text: `${a.role} lost (airframe failure). Reassign its mission role.`,
      });
    }
  }

  // 2) Live AA radar tracks — threat warning before a loss occurs.
  if (aa && Array.isArray(aa.tracked) && aa.tracked.length > 0) {
    const names = aa.tracked.map((id) => roleOf(state, id)).join(', ');
    advisories.push({
      level: 'warning',
      text: `THREAT: AA radar locking ${names}. Break engagement / take evasive action.`,
    });
  }

  // 3) Mission / kill-chain status.
  if (ev.kill_confirmed) {
    advisories.push({
      level: 'success',
      text: 'SUCCESS: Target neutralized. Mission objective complete.',
    });
  } else if (ev.broadcast) {
    advisories.push({
      level: 'info',
      text: 'INTEL: Target acquired by Recon. Coordinates broadcast to strike group — clear to engage.',
    });
  } else {
    advisories.push({
      level: 'info',
      text: 'STANDBY: Recon en route to observation point; strike group holding in loiter.',
    });
  }

  // 4) Comms degradation.
  const ns = typeof state.network_survivability === 'number' ? state.network_survivability : 1;
  const degraded = state.agents.filter((a) => !a.link_good && !a.lost);
  if (degraded.length > 0) {
    advisories.push({
      level: 'warning',
      text: `COMMS: Degraded link on ${degraded.map((a) => a.role).join(', ')}. Network survivability ${(ns * 100).toFixed(0)}%.`,
    });
  }

  // 5) Attrition / abort threshold.
  const lost = ev.total_lost || 0;
  if (lost >= 3) {
    advisories.push({
      level: 'critical',
      text: `ATTRITION: ${lost}/6 units lost. Recommend mission abort and regroup.`,
    });
  }

  return advisories;
}

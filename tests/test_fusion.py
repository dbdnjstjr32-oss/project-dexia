"""Fusion verification — multi-source sensor fusion (AIP build #2).

Proves fusion is *real*, not a relabel: feeds see different, imperfect slices;
the engine corroborates them so a track's position sharpens, confidence rises,
and provenance accrues — then decays (coasts) when the track goes unseen.

Headline narrative (the SA-11 story): SIGINT alone yields a vague emitter
(conf 0.40, ±150 m); when an EO fix corroborates it the track collapses to a
confident air-defense site (conf 0.82, ±5 m, sources=[sigint,uav_eo]); when both
feeds drop it, the track coasts and goes stale.

Dual-mode: ``pytest`` *and* ``python tests/test_fusion.py`` (prints the story).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.fusion import FusionEngine, PlatformSensorFeed, SigintFeed, WorldState
from dexia.fusion.feeds import build_feeds
from dexia.fusion.world import Entity
from dexia.scenario import load_catalog, load_scenario

_CAT = load_catalog()


def _rng():
    return np.random.default_rng(0)


# --------------------------------------------------------------------------- #
def test_feeds_see_different_imperfect_slices():
    """EO sees ground + air-defense in range; UGS is ground-only and blind to
    emitters; SIGINT only finds active emitters."""
    world = WorldState([
        Entity("t72_near", "t72_tank", "red", "armor", [3000, 0]),
        Entity("t72_far", "t72_tank", "red", "armor", [9000, 0]),
        Entity("sa11", "sa11_sam", "red", "air_defense", [4000, 0], emitting=True),
        Entity("tb2", "tb2_recon_uav", "blue", "isr", [0, 0]),
        Entity("ugs", "ugs_field", "blue", "sensor", [3000, 0]),
    ], _CAT)

    eo = {d.truth_id for d in PlatformSensorFeed("uav_eo", "eo_ir", 0.7).observe(world, 1, _rng())}
    assert eo == {"t72_near", "sa11"}            # t72_far out of 8 km range

    ugs = {d.truth_id for d in PlatformSensorFeed("ugs", "acoustic", 0.55).observe(world, 1, _rng())}
    assert ugs == {"t72_near"}                   # acoustic can't hear an emitter

    sig = SigintFeed(0.4).observe(world, 1, _rng())
    assert {d.truth_id for d in sig} == {"sa11"} and sig[0].category == "emitter"


def test_corroboration_sharpens_and_raises_confidence():
    world = WorldState([
        Entity("sa11", "sa11_sam", "red", "air_defense", [5000, 1500], emitting=True),
        Entity("tb2", "tb2_recon_uav", "blue", "isr", [0, 1500]),
    ], _CAT)
    sig, eo = SigintFeed(0.4), PlatformSensorFeed("uav_eo", "eo_ir", 0.7)
    fe, rng = FusionEngine(), _rng()

    # 1) SIGINT alone: one vague emitter
    fe.update(sig.observe(world, 1, rng), 1)
    t = fe.tracks[0]
    assert t.category == "emitter" and t.sources == ["sigint"]
    assert t.confidence == 0.4 and t.uncertainty_r == 150.0

    # 2) EO corroborates: same track sharpens + refines to air_defense
    fe.update(sig.observe(world, 2, rng) + eo.observe(world, 2, rng), 2)
    assert len(fe.tracks) == 1                    # associated, not duplicated
    t = fe.tracks[0]
    assert t.category == "air_defense"
    assert t.sources == ["sigint", "uav_eo"]
    # 1 - (1 - r_eff[sigint,2 looks])(1 - r_eff[eo,1 look]) = 1 - (1-0.45)(1-0.7)
    assert t.confidence == 0.835                   # dwell: sigint's 2nd look adds a little
    assert t.uncertainty_r <= 5.5                  # collapsed toward the EO fix


def test_sustained_single_feed_isr_raises_confidence():
    """Persistent ISR from one EO sensor must *confirm* a track — confidence climbs
    from the single-look prior (0.70) toward the dwell ceiling and crosses the 0.8
    strike threshold within a few looks. (Regression guard: a held single-sensor
    track previously stayed pinned at 0.70 forever, so the kill chain stalled.)"""
    world = WorldState([
        Entity("t72", "t72_tank", "red", "armor", [1200, 0]),
        Entity("tb2", "tb2_recon_uav", "blue", "isr", [0, 0]),
    ], _CAT)
    eo = PlatformSensorFeed("uav_eo", "eo_ir", 0.7)
    fe, rng = FusionEngine(), _rng()

    fe.update(eo.observe(world, 1, rng), 1)
    first = fe.tracks[0].confidence
    assert first == 0.7                              # first look == the prior, unchanged
    assert fe.tracks[0].sources == ["uav_eo"]        # single sensor only

    confs = [first]
    for tk in range(2, 12):                          # the UAV keeps eyes on it
        fe.update(eo.observe(world, tk, rng), tk)
        confs.append(fe.tracks[0].confidence)

    assert all(b >= a for a, b in zip(confs, confs[1:]))   # monotonic, never drops
    assert confs[-1] > 0.8                            # confirmed enough to strike
    assert confs[-1] <= 0.95                          # but never reaches certainty alone


def test_track_coasts_when_unseen():
    world = WorldState([
        Entity("sa11", "sa11_sam", "red", "air_defense", [5000, 1500], emitting=True),
        Entity("tb2", "tb2_recon_uav", "blue", "isr", [0, 1500]),
    ], _CAT)
    sig, eo = SigintFeed(0.4), PlatformSensorFeed("uav_eo", "eo_ir", 0.7)
    fe, rng = FusionEngine(), _rng()
    fe.update(sig.observe(world, 1, rng) + eo.observe(world, 1, rng), 1)
    peak = fe.tracks[0].confidence
    for tk in range(2, 31):                        # nothing reports it anymore
        fe.update([], tk)
    t = fe.tracks[0]
    assert t.confidence < peak and t.status == "stale"
    assert fe.active_tracks() == []                # dropped from the live picture


def test_seed_scenario_end_to_end():
    """Run the seed scenario through world -> feeds -> fusion for 12 ticks."""
    sc = load_scenario("ua-east-armor-thrust-007")
    world = WorldState.from_scenario(sc, _CAT)
    feeds = build_feeds(sc.feeds, _CAT)
    fe, rng = FusionEngine(), _rng()
    for tk in range(1, 13):
        world.step(1.0)
        dets = []
        for f in feeds:
            dets += f.observe(world, tk, rng)
        fe.update(dets, tk)

    tracks = fe.active_tracks()
    cats = {t.category for t in tracks}
    assert "armor" in cats                          # T-72 column tracked (EO/UGS)
    # SA-11 / Krasukha radiate but sit outside passive EO range -> SIGINT-only
    # emitter cuts (vague). Confirming them is the agent's job (task_isr), proved
    # in test_agent_loop; here we just assert the emitter shows up from SIGINT.
    emitters = [t for t in tracks if t.category == "emitter"]
    assert emitters and all(t.sources == ["sigint"] for t in emitters)


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA AIP build #2 — Multi-source Fusion (the SA-11 story)")
    print(bar)

    world = WorldState([
        Entity("sa11", "sa11_sam", "red", "air_defense", [5000, 1500], emitting=True),
        Entity("tb2", "tb2_recon_uav", "blue", "isr", [0, 1500]),
    ], _CAT)
    sig, eo = SigintFeed(0.4), PlatformSensorFeed("uav_eo", "eo_ir", 0.7)
    fe, rng = FusionEngine(), _rng()

    fe.update(sig.observe(world, 1, rng), 1)
    t = fe.tracks[0]
    print(f"\n[t=1] SIGINT only   → {t.track_id} {t.category:<11} "
          f"conf={t.confidence} ±{t.uncertainty_r}m sources={t.sources}")

    fe.update(sig.observe(world, 2, rng) + eo.observe(world, 2, rng), 2)
    t = fe.tracks[0]
    corroborated = t.confidence
    print(f"[t=2] +EO corroborate→ {t.track_id} {t.category:<11} "
          f"conf={t.confidence} ±{t.uncertainty_r}m sources={t.sources}  "
          f"(tracks={len(fe.tracks)} — merged, not duplicated)")

    for tk in range(3, 31):
        fe.update([], tk)
    t = fe.tracks[0]
    print(f"[coast] feeds drop  → {t.track_id} {t.category:<11} "
          f"conf={t.confidence} status={t.status}")

    print(f"\n[seed scenario] ua-east-armor-thrust-007 → 12 ticks")
    sc = load_scenario("ua-east-armor-thrust-007")
    w = WorldState.from_scenario(sc, _CAT)
    feeds = build_feeds(sc.feeds, _CAT)
    fe2, rng2 = FusionEngine(), _rng()
    for tk in range(1, 13):
        w.step(1.0)
        dets = []
        for f in feeds:
            dets += f.observe(w, tk, rng2)
        fe2.update(dets, tk)
    for t in sorted(fe2.active_tracks(), key=lambda x: -x.confidence):
        print(f"      {t.track_id:<14} {t.category:<11} conf={t.confidence:<5} "
              f"±{t.uncertainty_r:<5}m sources={t.sources}")

    ok = corroborated >= 0.8
    print("\n" + bar)
    print(f"FUSION {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(SA-11 corroboration drove conf {corroborated} from 2 independent sources)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Data-spine verification — equipment catalog + scenario format (AIP build #1).

Proves the declarative foundation: the equipment catalog loads into typed
capability specs, the seed scenario loads and validates against it, and the
validator actually catches malformed scenarios (wrong side, unknown equipment,
missing victory). No physics, no Ray — pure data.

Dual-mode: ``pytest tests/test_scenario_catalog.py`` *and* direct
(``python tests/test_scenario_catalog.py``, which prints the catalog + scenario).
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

from dexia.scenario import Scenario, load_catalog, load_scenario
from dexia.scenario.scenario import SCENARIO_DIR

_CATALOG = load_catalog()
_SEED = "ua-east-armor-thrust-007"


# --------------------------------------------------------------------------- #
def test_catalog_loads_typed_specs():
    assert len(_CATALOG) >= 10, "starter catalog should span a dozen-ish platforms"
    m777 = _CATALOG.get("m777_howitzer")
    assert m777 is not None and m777.side == "blue" and m777.role == "fires"
    fire = m777.effect("indirect_fire")
    assert fire is not None and fire.range_m == 30000 and fire.in_range(10000)
    assert not fire.in_range(2000)  # inside min_range


def test_catalog_capability_queries():
    # SIGINT-findable emitters: SA-11, S-300, Krasukha all radiate
    emitters = {s.key for s in _CATALOG.emitters()}
    assert {"sa11_sam", "s300_sam", "krasukha_ew"} <= emitters
    assert _CATALOG.get("sa11_sam").detectable_by("sigint")
    # TB2 EO sensor sees armor; UGS acoustic does not see emitters
    tb2 = _CATALOG.get("tb2_recon_uav").provides_sensor("eo_ir")
    assert tb2 is not None and tb2.can_detect("armor")
    ugs = _CATALOG.get("ugs_field").provides_sensor("acoustic")
    assert ugs is not None and not ugs.can_detect("air_defense")


def test_seed_scenario_loads_and_validates():
    sc = load_scenario(_SEED)  # raises if invalid
    assert sc.id == _SEED and sc.theater == "eastern_europe"
    assert sc.mission.intent == "delay"
    assert sc.mission.victory.get("hold_line") == [4200, 0]
    blue = {f.cls for f in sc.blue}
    assert {"m777_howitzer", "tb2_recon_uav", "switchblade", "ew_jammer_gnd"} <= blue
    # 8 T-72 + 4 BMP advancing
    t72 = next(f for f in sc.red if f.cls == "t72_tank")
    assert t72.n == 8 and t72.behavior == "advance"
    assert sc.validate(_CATALOG) == []


def test_validator_catches_bad_scenarios():
    # red tank placed on the blue force, unknown equipment, missing victory, bad feed
    bad = Scenario.from_dict({
        "id": "broken",
        "mission": {"intent": "delay", "victory": {}},
        "blue": [{"cls": "t72_tank", "n": 1}, {"cls": "phaser_cannon", "n": 1}],
        "red": [],
        "feeds": ["telepathy"],
    })
    problems = bad.validate(_CATALOG)
    joined = " | ".join(problems)
    assert "t72_tank' is a red platform on the blue force" in joined
    assert "phaser_cannon' not in catalog" in joined
    assert "victory is empty" in joined
    assert "unknown feed 'telepathy'" in joined


def test_all_library_scenarios_valid():
    """Every scenario shipped in the library must validate (CI guard for the
    generated library later)."""
    from dexia.scenario.scenario import list_scenarios
    paths = list_scenarios()
    assert paths, "expected at least the seed scenario"
    for p in paths:
        sc = load_scenario(p, validate=False)
        assert sc.validate(_CATALOG) == [], f"{os.path.basename(p)} invalid"


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA AIP build #1 — Equipment Catalog + Scenario data spine")
    print(bar)

    print(f"\n[1] equipment_catalog.yaml → {len(_CATALOG)} platforms")
    for s in _CATALOG.all():
        caps = []
        if s.sensors:
            caps.append("sensors:" + ",".join(se.type for se in s.sensors))
        if s.effects:
            caps.append("effects:" + ",".join(e.type for e in s.effects))
        if s.emits:
            caps.append("emits:" + s.emits.get("type", "?"))
        print(f"      {s.side:<4} {s.key:<16} {s.role:<12} {' '.join(caps)}")

    print(f"\n[2] load + validate scenario '{_SEED}'")
    sc = load_scenario(_SEED)
    print(f"      theater={sc.theater}  intent={sc.mission.intent}")
    print(f"      tasking={sc.mission.tasking}")
    print(f"      victory={sc.mission.victory}")
    print(f"      blue: {[f'{f.cls}x{f.n}' for f in sc.blue]}")
    print(f"      red:  {[f'{f.cls}x{f.n}' for f in sc.red]}")
    print(f"      feeds={sc.feeds}  validate()={sc.validate(_CATALOG) or 'OK'}")

    print(f"\n[3] validator rejects a broken scenario")
    bad = Scenario.from_dict({
        "id": "broken", "mission": {"intent": "delay", "victory": {}},
        "blue": [{"cls": "t72_tank", "n": 1}], "red": [], "feeds": ["telepathy"],
    })
    for p in bad.validate(_CATALOG):
        print(f"      ✗ {p}")

    ok = (len(_CATALOG) >= 10 and sc.validate(_CATALOG) == [] and bad.validate(_CATALOG))
    print("\n" + bar)
    print(f"DATA SPINE {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"({len(_CATALOG)} platforms, seed scenario valid, validator catches errors)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

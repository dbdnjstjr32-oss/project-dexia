"""Phase-6 verification: dynamic MuJoCo builder ("Drone Garage").

Loads drone_profiles.json, generates an MJCF for each profile via
generate_mjcf(), and compiles it with mujoco.MjModel.from_xml_string() — proving
the frontend-authored profiles produce valid physics models with no errors.
Also prints the full MJCF for a Tandem profile.

Run (Python 3.13 — mujoco installed there):
    python test_phase6_builder.py
"""

from __future__ import annotations

import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import mujoco

from dexia.physics.mujoco_builder import generate_mjcf, profile_summary

PROFILES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drone_profiles.json")


def main() -> int:
    print("=" * 74)
    print("PHASE 6 BUILDER TEST — generate_mjcf() + MjModel.from_xml_string()")
    print("=" * 74)

    with open(PROFILES_PATH, "r", encoding="utf-8") as f:
        profiles = json.load(f)
    print(f"\nLoaded {len(profiles)} profiles from drone_profiles.json\n")

    print(f"{'name':<26} {'topo':<7} {'motors':>6} {'mass':>6} {'T/W':>5} "
          f"{'Ixx':>8} {'Iyy':>8} {'Izz':>8}  compile")
    print("-" * 92)

    all_ok = True
    tandem_xml = None
    tandem_name = None
    for p in profiles:
        try:
            xml = generate_mjcf(p)
            model = mujoco.MjModel.from_xml_string(xml)
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)  # one physics eval to catch dynamics errors

            s = profile_summary(p)
            body_mass = float(model.body_mass.sum())
            mass_ok = abs(body_mass - s["mass"]) < 1e-3
            njnt_ok = model.njnt == 1            # exactly one free joint
            nu_ok = model.nu == s["n_motors"]    # actuator count matches motors
            ok = mass_ok and njnt_ok and nu_ok
            ixx, iyy, izz = s["diag_inertia"]
            print(f"{s['name']:<26} {s['topology']:<7} {model.nu:>6} {s['mass']:>6.2f} "
                  f"{s['thrust_to_weight']:>5.2f} {ixx:>8.5f} {iyy:>8.5f} {izz:>8.5f}  "
                  f"{'OK' if ok else 'MISMATCH'}")
            all_ok = all_ok and ok

            if s["topology"] == "tandem" and tandem_xml is None:
                tandem_xml = xml
                tandem_name = s["name"]
        except Exception as e:
            all_ok = False
            print(f"{p.get('name','?'):<26} {p.get('topology','?'):<7}  COMPILE FAILED: {e}")

    # ---- print a full Tandem MJCF ----
    if tandem_xml:
        print("\n" + "=" * 74)
        print(f"GENERATED MJCF — Tandem profile: “{tandem_name}”")
        print("=" * 74)
        print(tandem_xml)

    print("=" * 74)
    print(f"PHASE 6 BUILDER TEST: {'PASS' if all_ok else 'FAIL'}")
    print("=" * 74)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

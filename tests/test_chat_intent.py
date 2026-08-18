"""Commander chat intent routing — the AIP must *listen*, not just talk.

Regression guard for the bug where every chat message was forced through the COA
planner, so 'recall the drone' / 'how many assets?' were ignored. These paths are
deterministic (no LLM), so the test runs offline.
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

from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.red_commander import RedCommander
from dexia.agent.mission_manager import MissionManager


def _mgr():
    w = BattleGenerator().generate(
        blue_forces={"tb2_recon_uav": 1, "switchblade": 1, "m777_howitzer": 2},
        difficulty="easy")
    return MissionManager(w, RedCommander())


def test_intent_classification():
    mm = _mgr()
    cases = {
        "드론 회수해": "recall", "기지로 복귀시켜": "recall",
        "가용할 수 있는 자산 몇 개 있냐": "status", "전력 현황 알려줘": "status",
        "정찰 보내": "recon", "북쪽 수색해": "recon",
        "저 표적 타격해": "strike", "사격 방안 줘": "strike",
        "전과 평가해": "bda",
        "조금 더 신중하게 가자": None,           # free chat → planner fallback
    }
    for text, expected in cases.items():
        assert mm._classify_intent(text) == expected, text


def test_asset_query_answers_from_real_state():
    mm = _mgr()
    before = len(mm.approval_queue)
    mm.modify_plan("가용 자산 몇 개 있어?")
    last = mm.aip_feed[-1]
    assert last["type"] == "ANALYSIS"
    assert "가용 자산 현황" in last["message"]
    assert "tb2_recon_uav" in last["message"] and "switchblade" in last["message"]
    assert "engage" in last["message"]               # switchblade's real capability
    assert len(mm.approval_queue) == before          # a question must NOT wipe the plan


def test_recall_flies_drones_home():
    mm = _mgr()
    uav = next(e for e in mm.world.blue if "tb2" in e.entity_id)
    home = list(mm._home[uav.entity_id])
    # send it somewhere first, then recall
    uav.position = [home[0] + 4000, home[1] + 4000] + list(uav.position[2:])
    mm.modify_plan("드론 회수해")
    last = mm.aip_feed[-1]
    assert last["type"] == "EXECUTION" and "RTB" in last["message"]
    assert uav.entity_id in last["message"]
    # the asset is now routed back toward its rally point
    dest = (getattr(uav, "_motion", None) and uav._motion.route[-1]) or (uav.route and uav.route[-1])
    assert dest is not None
    assert abs(dest[0] - home[0]) < 1.0 and abs(dest[1] - home[1]) < 1.0


def test_recall_no_mobile_sensor_reports_cleanly():
    mm = BattleGenerator().generate(blue_forces={"m777_howitzer": 2}, difficulty="easy")
    mm = MissionManager(mm, RedCommander())
    mm.modify_plan("드론 회수")
    assert mm.aip_feed[-1]["type"] == "ERROR"

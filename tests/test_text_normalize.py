"""Hanja-bleed normalization — Han ideographs in the Korean feed map to Hangul."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dexia.agent.text_normalize import contains_han, normalize_korean, normalize_obj


def test_observed_leak_is_converted():
    # the exact leak captured live: "不明瞭한 추적 ..." -> "불명료한 추적 ..."
    src = "不明瞭한 추적 TRK-EMI-002를 確認하기 위해 ISR을 推薦합니다."
    out = normalize_korean(src)
    assert not contains_han(out), out
    assert "불명료한" in out and "확인" in out and "추천" in out


def test_pure_hangul_untouched():
    src = "현재 TRK-APC-003은 신뢰성 낮은 추적으로, 감시가 필요합니다."
    assert normalize_korean(src) == src


def test_single_char_fallback():
    # a word not in the phrase map still degrades to Hangul, never staying Han
    assert not contains_han(normalize_korean("敵 區域 火力 支援"))


def test_ascii_and_ids_preserved():
    src = "COA-1 m777_howitzer_0 → request_fires on TRK-APC-003 (砲擊)"
    out = normalize_korean(src)
    assert "COA-1" in out and "m777_howitzer_0" in out and "request_fires" in out
    assert "포격" in out and not contains_han(out)


def test_normalize_obj_recurses_feed_and_coas():
    data = {
        "feed": [{"type": "ANALYSIS", "message": "不確實한 對象 確認 필요"}],
        "coas": [{"action": "request_fires", "description": "目標 砲擊 推薦"}],
    }
    out = normalize_obj(data)
    assert not contains_han(out["feed"][0]["message"])
    assert not contains_han(out["coas"][0]["description"])
    assert out["coas"][0]["action"] == "request_fires"   # canonical name untouched

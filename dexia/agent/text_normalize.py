"""Korean output normalization — strip the CJK-hanja bleed from the LLM feed.

The tactical staff officer is prompted to write Korean, but the small local model
(gpt-oss:20b) intermittently emits Sino-Korean words in **Han ideographs** instead
of Hangul — e.g. ``不明瞭한 추적`` for ``불명료한 추적``. In a Korean operations feed
*any* Han ideograph is an error, so this module deterministically maps them back to
their Hangul reading before the text is shown.

Two passes, applied to every LLM-authored string (feed messages + COA descriptions):
  1. phrase map  — multi-character Sino-Korean words (longest-first) for the cases
                   the model actually leaks; gives the correct contextual reading.
  2. char map    — single-hanja fallback (dominant Korean reading) so a word the
                   phrase map missed still degrades to Hangul rather than staying Han.

The domain (military COA narration) is narrow, so a curated table covers it well.
Extend ``_PHRASES`` / ``_CHARS`` when a new leak is observed; ``contains_han`` lets
callers log anything still uncovered.
"""

from __future__ import annotations

import re

# --- multi-char Sino-Korean words the model leaks (longest-match first) -------- #
_PHRASES = {
    "不明瞭": "불명료", "明瞭": "명료", "不確實": "불확실", "確實": "확실",
    "不可能": "불가능", "可能": "가능", "不明": "불명",
    "確認": "확인", "目標": "목표", "位置": "위치", "移動": "이동",
    "攻擊": "공격", "防禦": "방어", "偵察": "정찰", "砲擊": "포격",
    "推薦": "추천", "分析": "분석", "狀況": "상황", "機械": "기계",
    "信賴": "신뢰", "監視": "감시", "危險": "위험", "行動": "행동",
    "對象": "대상", "停止": "정지", "發信": "발신", "高度": "고도",
    "速度": "속도", "距離": "거리", "射擊": "사격", "彈着": "탄착",
    "再配置": "재배치", "再偵察": "재정찰", "格滅": "격멸", "生存": "생존",
    "卽時": "즉시", "現在": "현재", "追跡": "추적", "情報": "정보",
}

# --- single-hanja fallback (dominant Korean reading in this domain) ------------ #
_CHARS = {
    "不": "불", "明": "명", "瞭": "료", "確": "확", "認": "인", "實": "실",
    "目": "목", "標": "표", "位": "위", "置": "치", "移": "이", "動": "동",
    "攻": "공", "擊": "격", "防": "방", "禦": "어", "偵": "정", "察": "찰",
    "砲": "포", "推": "추", "薦": "천", "分": "분", "析": "석", "狀": "상",
    "況": "황", "機": "기", "械": "계", "信": "신", "賴": "뢰", "監": "감",
    "視": "시", "危": "위", "險": "험", "行": "행", "對": "대", "象": "상",
    "停": "정", "發": "발", "體": "체", "性": "성", "高": "고", "低": "저",
    "速": "속", "度": "도", "距": "거", "離": "리", "敵": "적", "中": "중",
    "戰": "전", "鬪": "투", "彈": "탄", "射": "사", "着": "착", "再": "재",
    "配": "배", "格": "격", "滅": "멸", "生": "생", "卽": "즉", "時": "시",
    "現": "현", "在": "재", "追": "추", "跡": "적", "情": "정", "報": "보",
    "可": "가", "能": "능", "的": "적", "區": "구", "域": "역", "前": "전",
    "後": "후", "左": "좌", "右": "우", "北": "북", "南": "남", "東": "동",
    "西": "서", "火": "화", "力": "력", "支": "지", "援": "원", "完": "완",
    "了": "료", "成": "성", "功": "공", "失": "실", "敗": "패", "判": "판",
    "定": "정", "結": "결", "果": "과",
}

# CJK Unified Ideographs (+ common extensions) — what counts as "still Han".
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def contains_han(text: str) -> bool:
    """True if any Han ideograph remains (useful for logging uncovered leaks)."""
    return bool(_HAN_RE.search(text or ""))


def normalize_korean(text: str) -> str:
    """Convert any leaked Han ideographs in ``text`` to their Hangul reading."""
    if not text or not contains_han(text):
        return text
    for han, kor in _PHRASES.items():           # dict preserves longest-first order
        if han in text:
            text = text.replace(han, kor)
    if contains_han(text):                       # single-char fallback for the rest
        text = "".join(_CHARS.get(ch, ch) for ch in text)
    return text


def normalize_obj(obj):
    """Recursively normalize every string in a feed/COA dict or list in place-ish."""
    if isinstance(obj, str):
        return normalize_korean(obj)
    if isinstance(obj, dict):
        return {k: normalize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_obj(v) for v in obj]
    return obj

# -*- coding: utf-8 -*-
"""안내 규정을 장 단위로 쪼개고, 카테고리에 필요한 장만 골라 컨텍스트를 만든다.

전문을 넣지 않는 이유: 관련 없는 규정이 오답을 유도한다
(가격 문의에 위생 규정이 섞여 들어가는 식).
"""
import re
from pathlib import Path

BASE = Path(__file__).parent
POLICY = BASE / "docs" / "policy_estate.md"


def split_sections(text):
    """'## ' 헤딩 단위로 쪼갠다. 키는 장 번호, 부록은 제외."""
    parts = re.split(r"^## ", text, flags=re.M)
    out = {"_header": parts[0].strip()}
    for p in parts[1:]:
        title = p.split("\n", 1)[0].strip()
        if title.startswith("부록"):
            continue
        m = re.match(r"(\d+)\.", title)
        out[m.group(1) if m else title] = "## " + p.rstrip()
    return out


SECTION_MAP = {
    "AUCTION": ["2"],        # 2장 경매 규정
    "PRICE": ["3"],          # 3장 시세 규정
    "REDEVELOP": ["4"],      # 4장 정비사업 규정
    "REGULATION": ["5"],     # 5장 규제 규정
    "OUT_OF_SCOPE": ["7"],   # 7장 범위 밖 안내
}

ALWAYS = ["0", "1", "6"]     # 쓰는 법 · 카테고리 정의 · 검증/이관 (항상 포함)


def build_context(route, secs=None):
    """카테고리에 필요한 규정 조각만 이어 붙인다."""
    secs = sections if secs is None else secs
    keys = [k for k in ALWAYS + SECTION_MAP.get(route, []) if k in secs]
    return "\n\n".join([secs["_header"]] + [secs[k] for k in keys])


FIXED_POLICY = {
    "conf_threshold": 0.5,   # 6장: 확신도 임계값
    "deposit_rate": 10,      # 2장: 보증금은 최저매각가격의 10%
}

sections = split_sections(POLICY.read_text(encoding="utf-8"))


def build_answer_prompt(question, route, tool_results=None):
    """답변 생성용 시스템 프롬프트를 조립한다 (LLM 답변용)."""
    import json
    from prompts import ANSWER_RULES
    ctx = build_context(route)
    tr = json.dumps(tool_results or {}, ensure_ascii=False, indent=1)
    return (f"{ANSWER_RULES}\n"
            f"===== 안내 규정 (카테고리: {route}) =====\n{ctx}\n\n"
            f"===== 조회 결과 =====\n{tr}\n")

# -*- coding: utf-8 -*-
"""파이프라인: route → answer → guard → (END | escalate).

분류와 이관 판단을 분리했고, 답변은 DB 조회 값으로만 조립한다
(기본값은 결정적 엔진 — API 키 없이도 evaluate.py가 돈다).
OPENAI_API_KEY가 있으면 prompts.py 지침으로 LLM 분류·답변을 쓸 수 있다.
"""
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from context import FIXED_POLICY
from tools import (DB, escalate_to_agent, find_case_no, find_dong, find_price_name,
                   find_redev_name, get_auction_detail, get_live_price, get_real_price,
                   get_redevelop_info, get_regulation_info, search_property)

CONF_THRESHOLD = FIXED_POLICY["conf_threshold"]
HANDOFF = "정확한 확인을 위해 상담원에게 연결해 드리겠습니다."
OUT_MSG = "해당 내용은 저희 안내 범위를 벗어나 확인이 어렵습니다. " + HANDOFF

# 우선순위대로 검사한다 (규정 1장: 범위밖 > 규제 > 정비사업 > 경매 > 시세)
# AUCTION이 PRICE보다 앞선다: "최저매각가격"이 "가격"을 포함하기 때문이다 (#1)
RULES = [
    (r"맛집|식당|떡볶이|대출|주식|날씨|투자|추천|유망|수익|예측|점쳐|번역", "OUT_OF_SCOPE"),
    (r"용도지역|용적률|건폐율|토지거래|지구단위|규제|허가구역", "REGULATION"),
    (r"재건축|재개발|정비사업|조합|관리처분|사업시행|입주|준공|분양가|분담금", "REDEVELOP"),
    (r"경매|공매|타경|감정가|최저|매각|기일|보증금|유찰|입찰|명도|권리|낙찰", "AUCTION"),
    (r"실거래|시세|매매가|전세가|거래가|전세금|전세|월세|얼마에", "PRICE"),
]

MISSING = r"명도|권리분석|점유|세입자|낙찰가|분담금|분양가"  # DB 미등록 → 조회 후 이관


def classify(question):
    """규칙 분류. (LLM 버전은 llm_classify — 키 있을 때만)"""
    for pattern, route in RULES:
        if re.search(pattern, question):
            return {"route": route, "confidence": 0.85, "reason": "키워드 규칙 매치"}
    return {"route": "OUT_OF_SCOPE", "confidence": 0.35, "reason": "매치되는 규칙 없음"}


def gemini_classify(question, model="gemini-flash-latest"):
    """LLM 분류 — GEMINI_API_KEY가 있을 때 쓴다. 추가 의존성 없이 stdlib로 호출."""
    import json as _json
    import os as _os
    import urllib.request as _url
    from prompts import ROUTE_GUIDE
    key = _os.environ["GEMINI_API_KEY"]
    prompt = (ROUTE_GUIDE + "\n고객 문의: " + question +
              '\n반환 형식(JSON만, 코드펜스 없이): {"route": "AUCTION|PRICE|REDEVELOP|'
              'REGULATION|OUT_OF_SCOPE", "confidence": 0.0-1.0, "reason": "한 문장"}')
    req = _url.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=" + key,
        data=_json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with _url.urlopen(req, timeout=60) as r:
        t = _json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
    t = re.sub(r"^```json|^```|```$", "", t.strip(), flags=re.M).strip()
    d = _json.loads(t)
    return {"route": d["route"], "confidence": float(d["confidence"]), "reason": d.get("reason", "")}


def llm_classify(question):
    """LLM 분류 — 키가 있을 때만 쓴다. 없으면 규칙으로 떨어진다."""
    import os
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return gemini_classify(question)
        except Exception:
            return classify(question)  # 쿼터 초과 등은 규칙으로 폴백
    if not os.environ.get("OPENAI_API_KEY"):
        return classify(question)
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel, Field
    from typing import Literal
    from prompts import ROUTE_GUIDE

    class RouteDecision(BaseModel):
        route: Literal["AUCTION", "PRICE", "REDEVELOP", "REGULATION", "OUT_OF_SCOPE"]
        confidence: float = Field(ge=0.0, le=1.0)
        reason: str

    d = init_chat_model("gpt-4o-mini", temperature=0).with_structured_output(RouteDecision).invoke(
        [("system", ROUTE_GUIDE), ("human", f"고객 문의: {question}")])
    return {"route": d.route, "confidence": d.confidence, "reason": d.reason}


def gate(route, confidence):
    if confidence < CONF_THRESHOLD:
        return "ESCALATE"
    if route == "OUT_OF_SCOPE":
        return "OUT_OF_SCOPE"
    return "HANDLE"


def won(n):
    return f"{n:,}원"


def ask_candidates(sr):
    names = [c["label"].split(" ", 1)[0] if " " in c["label"] else c["label"] for c in sr["candidates"][:3]]
    return (f"해당 조건의 물건이 {len(sr['candidates'])}건이 있습니다({', '.join(names)}). "
            f"어느 물건을 안내해 드릴까요?")


def answer(question, route):
    """카테고리별 조회 + 근거 조립. 반환: (답변, 도구결과, 행동)."""
    if route == "OUT_OF_SCOPE":
        return OUT_MSG, {}, "OUT_OF_SCOPE"
    if re.search(MISSING, question):  # DB 미등록 항목 → 근거를 읽고 넘긴다
        sr = search_property(question)
        what = re.search(MISSING, question).group(0)
        return (f"{what} 관련 정보는 등록된 정보가 없어 확인이 어렵습니다. "
                + HANDOFF), {"search_property": sr}, "ESCALATE"

    if route == "AUCTION":
        sr = search_property(question)
        rid = sr["resolved_id"] if sr["resolved_kind"] == "AUCTION" else None
        if not rid:
            if sr["ambiguous"]:
                return ask_candidates(sr), {"search_property": sr}, "ASK"
            return ("등록된 경매 물건에서 찾지 못했습니다. "
                    "정확한 사건번호를 알려주시겠어요?"), {"search_property": sr}, "ASK"
        res = {"search_property": sr, "get_auction_detail": get_auction_detail(auction_id=rid)}
        d = res["get_auction_detail"]
        return (f"{d['case_no']}({d['address']}, {d['kind']})의 감정가는 {won(d['official_price'])}, "
                f"최저매각가격은 {won(d['min_price'])}이며 매각기일은 {d['maegak_date']}, "
                f"입찰보증금은 {won(d['deposit'])}입니다."), res, "ANSWER"

    if route == "PRICE":
        name = find_price_name(question)
        sr = search_property(question)
        rid = sr["resolved_id"] if sr["resolved_kind"] == "PRICE" else None
        if name or rid:
            pid = next((p["price_id"] for p in DB["prices"] if p["name"] == name), rid)
            res = {"search_property": sr, "get_real_price": get_real_price(price_id=pid)}
            p = res["get_real_price"]
            jeon = f", 전세금은 {won(p['jeonse'])}" if p["jeonse"] else ""
            return (f"{p['name']}({p['dong']}, {p['area_m2']}㎡) {p['date']} 매매 실거래가는 "
                    f"{won(p['trade_price'])}{jeon}입니다."), res, "ANSWER"
        dong = find_dong(question)
        if dong:
            res = {"get_real_price": get_real_price(dong=dong)}
            rows = res["get_real_price"]["trades"]
            if rows:
                lines = [f"· {r['name']}({r['area_m2']}㎡) {r['date']} {won(r['trade_price'])}" for r in rows]
                return f"{dong} 등록 거래는 다음과 같습니다.\n" + "\n".join(lines), res, "ANSWER"
            live = get_live_price(dong=dong)  # 목 DB에 없으면 실거래 스냅샷으로 폴백
            if live["count"]:
                res = {"get_live_price": live}
                lines = [f"· {r['name']}({r['area_m2']}㎡) {r['date']} {won(r['price'])}"
                         for r in live["trades"][:5]]
                return (f"{dong} 실거래(출처: 서울시 공개자료, 기준 {live['fetched_at']})는 "
                        f"다음과 같습니다.\n" + "\n".join(lines)), res, "ANSWER"
            return f"{dong}에 등록된 거래가 없습니다.", res, "ANSWER"
        return ("등록된 거래에서 찾지 못했습니다. "
                "단지명과 동 이름을 알려주시겠어요?"), {"search_property": sr}, "ASK"

    if route == "REDEVELOP":
        name = find_redev_name(question)
        if name:
            res = {"get_redevelop_info": get_redevelop_info(name=name)}
            d = res["get_redevelop_info"]
            done = f"이며 예상 준공은 {d['expected_completion']}입니다" if d["expected_completion"] else "입니다"
            return (f"{d['name']}({d['type']}, {d['address']})은 현재 {d['stage']} 단계{done}."), res, "ANSWER"
        dong = find_dong(question)
        if dong:
            res = {"get_redevelop_info": get_redevelop_info(dong=dong)}
            if "error" not in res["get_redevelop_info"]:
                d = res["get_redevelop_info"]
                return (f"{dong} 등록 구역은 {d['name']}({d['type']})이며 현재 {d['stage']} 단계입니다."), res, "ANSWER"
        sr = search_property(question)
        return ("등록된 정비사업 구역에서 찾지 못했습니다. "
                "구역명(예: 개포주공1단지)을 알려주시겠어요?"), {"search_property": sr}, "ASK"

    if route == "REGULATION":
        dong = find_dong(question)
        if dong:
            res = {"get_regulation_info": get_regulation_info(dong)}
            g = res["get_regulation_info"]
            if "error" in g:  # 등록 지역이 아님 → 되묻기 (수치 단정 금지, 규정 5장)
                return (f"{dong}은 등록된 규제 지역이 아닙니다. "
                        f"역삼동·잠실동·합정동 중 어느 지역을 안내해 드릴까요?"), res, "ASK"
            return (f"{g['area']}({g['gu']})은 {g['zone']}이며 용적률 {g['far']}%, 건폐율 {g['lcr']}%입니다. "
                    f"{g['land_tx_note']}"), res, "ANSWER"
        return ("어느 지역의 규제인지 알려주시겠어요? 동 이름(예: 역삼동)을 말씀해 주시면 "
                "용도지역·용적률·건폐율을 안내해 드립니다."), {}, "ASK"

    return HANDOFF, {"escalate_to_agent": escalate_to_agent("미분류", {"q": question})}, "ESCALATE"


def guardrail(answer_text, tool_results=None, min_check=1000):
    """답변 속 숫자의 출처를 역추적한다. 출처 불명이면 위반."""
    import json as _json
    allowed = set()
    for v in FIXED_POLICY.values():
        if isinstance(v, int):
            allowed.add(v)
    tool_nums = set()
    for r in (tool_results or {}).values():
        t = _json.dumps(r, ensure_ascii=False)
        t = re.sub(r"(?<=\d),(?=\d)", "", t)
        tool_nums |= {int(m) for m in re.findall(r"\d+", t)}
    allowed |= tool_nums
    base = sorted(allowed)
    for a in base:  # 한 단계 산술 유도값
        for b in base:
            allowed.add(a + b)
            if a > b:
                allowed.add(a - b)
    a = re.sub(r"(?<=\d),(?=\d)", "", answer_text)
    susp = sorted(n for n in {int(m) for m in re.findall(r"\d+", a)} if n >= min_check and n not in allowed)
    ok = not susp
    if re.search(r"명도|낙찰|허가|될 것|오를 것|안전", answer_text) and re.search(r"가능합니다|됩니다|유망합니다|안전합니다", answer_text):
        ok = False  # DB 미등록 항목 단정
        susp = susp + ["미등록 항목 단정"]
    return {"ok": ok, "violations": [] if ok else [{"type": "출처 불명 수치", "detail": str(susp)}]}


class GraphState(TypedDict, total=False):
    question: str
    use_llm: bool
    route: str
    confidence: float
    action: str
    answer: str
    tools: list
    results: dict
    guardrail_ok: bool


def node_route(state):
    c = llm_classify(state["question"]) if state.get("use_llm") else classify(state["question"])
    return {"route": c["route"], "confidence": c["confidence"], "action": gate(c["route"], c["confidence"])}


def node_answer(state):
    text, results, action = answer(state["question"], state["route"])
    return {"answer": text, "tools": list(results), "results": results,
            "action": action if state["action"] == "HANDLE" else state["action"]}


def node_guard(state):
    if state["action"] in ("ESCALATE", "OUT_OF_SCOPE"):
        msg = OUT_MSG if state["action"] == "OUT_OF_SCOPE" else None
        if state["action"] == "ESCALATE" and "answer" not in state:
            msg = escalate_to_agent("분류확신도미달", {"q": state["question"]})["message"]
        return {"guardrail_ok": True, **({"answer": msg} if msg else {})}
    ok = guardrail(state["answer"], state.get("results"))["ok"]
    return {"guardrail_ok": ok, "action": state["action"] if ok else "ESCALATE"}


def after_route(state):
    return "answer" if state["action"] == "HANDLE" else "guard"


def after_guard(state):
    if state["action"] == "ESCALATE" and "answer" not in state:
        return "guard"
    return END


def build_agent():
    g = StateGraph(GraphState)
    g.add_node("route", node_route)
    g.add_node("answer", node_answer)
    g.add_node("guard", node_guard)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", after_route, {"answer": "answer", "guard": "guard"})
    g.add_edge("answer", "guard")
    g.add_conditional_edges("guard", after_guard, {"guard": "guard", END: END})
    return g.compile()


agent_app = build_agent()


def customer_agent(question, use_llm=False):
    """문의 한 줄을 파이프라인에 통과시킨다."""
    out = agent_app.invoke({"question": question, "use_llm": use_llm})
    return {"question": question, "route": out.get("route"), "confidence": out.get("confidence"),
            "action": out.get("action"), "tools": out.get("tools", []),
            "results": out.get("results", {}), "answer": out.get("answer", ""),
            "guardrail_ok": out.get("guardrail_ok")}

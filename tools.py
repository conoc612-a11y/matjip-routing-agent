# -*- coding: utf-8 -*-
"""등록 물건 조회 도구. 안내 규정의 [DB 조회] 표시에서 도출했다."""
import json
import re
from pathlib import Path

DB = json.loads((Path(__file__).parent / "data" / "estate.json").read_text(encoding="utf-8"))
AUCTIONS = {a["auction_id"]: a for a in DB["auctions"]}
PRICES = {p["price_id"]: p for p in DB["prices"]}
REDEV = {d["redev_id"]: d for d in DB["redevelop"]}
REGS = {g["reg_id"]: g for g in DB["regulations"]}

INDEX = ([{"id": a["auction_id"], "kind": "AUCTION", "label": f"{a['case_no']} {a['address']} {a['kind']}"} for a in DB["auctions"]]
         + [{"id": p["price_id"], "kind": "PRICE", "label": f"{p['name']} {p['dong']} {p['gu']}"} for p in DB["prices"]]
         + [{"id": d["redev_id"], "kind": "REDEVELOP", "label": f"{d['name']} {d['dong']} {d['type']}"} for d in DB["redevelop"]])


def _toks(s):
    return [t for t in re.split(r"[\s·(),?.!~]+", s) if t]


def search_property(query):
    """사건번호·단지명·동 이름으로 등록 물건을 찾는다. 먼저 부르는 도구다."""
    qt = _toks(query)
    flat = query.replace(" ", "")
    hits = []
    for e in INDEX:
        blob = e["label"].replace(" ", "")
        n = sum(1 for t in qt if t and (t.replace(" ", "") in blob))
        m = re.search(r"\d+타경\d+", flat)
        if m and m.group(0) in blob:
            n += 2
        if n:
            hits.append({"id": e["id"], "kind": e["kind"], "label": e["label"], "score": n})
    hits.sort(key=lambda h: (-h["score"], h["id"]))
    top = [h for h in hits if hits and h["score"] == hits[0]["score"]]
    kinds = {h["kind"] for h in top}
    resolved = top[0]["id"] if len(top) == 1 else None
    return {"query": query, "candidates": hits[:5], "resolved_id": resolved,
            "resolved_kind": top[0]["kind"] if len(top) == 1 else None,
            "ambiguous": len(top) > 1,
            "note": "후보가 여러 개입니다. 어느 물건인지 고객에게 확인하십시오." if len(top) > 1 else None,
            "_kinds": sorted(kinds)}


def get_auction_detail(auction_id=None, case_no=None):
    """경매 물건의 감정가·최저매각가격·매각기일·보증금을 조회한다."""
    a = None
    if case_no:
        a = next((x for x in DB["auctions"] if x["case_no"] == case_no), None)
    elif auction_id:
        a = AUCTIONS.get(auction_id)
    if not a:
        return {"error": "물건을 찾을 수 없습니다", "auction_id": auction_id, "case_no": case_no}
    return {k: a[k] for k in ["auction_id", "case_no", "address", "kind", "official_price",
                              "min_price", "maegak_date", "deposit", "deposit_note", "times_failed"]}


def get_real_price(price_id=None, dong=None):
    """실거래가·전세금을 조회한다. 동 이름만 줘도 그 동의 등록 거래를 돌려준다."""
    if price_id:
        p = PRICES.get(price_id)
        if not p:
            return {"error": "거래를 찾을 수 없습니다", "price_id": price_id}
        return {k: p[k] for k in ["price_id", "name", "dong", "gu", "area_m2", "date",
                                  "trade_price", "jeonse"]}
    if dong:
        rows = [{k: p[k] for k in ["price_id", "name", "dong", "area_m2", "date",
                                   "trade_price", "jeonse"]}
                for p in DB["prices"] if p["dong"] == dong]
        return {"dong": dong, "trades": rows, "count": len(rows)}
    return {"error": "단지 또는 동 이름을 지정해 주세요"}


def get_redevelop_info(redev_id=None, dong=None, name=None):
    """정비사업 구역의 진행 단계·위치·예상 준공을 조회한다."""
    d = None
    if redev_id:
        d = REDEV.get(redev_id)
    elif name:
        d = next((x for x in DB["redevelop"] if x["name"] in name or name in x["name"]), None)
    elif dong:
        d = next((x for x in DB["redevelop"] if x["dong"] == dong), None)
    if not d:
        return {"error": "등록된 구역이 아닙니다", "redev_id": redev_id, "dong": dong, "name": name}
    return {k: d[k] for k in ["redev_id", "name", "type", "address", "stage", "expected_completion"]}


def get_regulation_info(dong):
    """동의 용도지역·용적률·건폐율·허가구역·지구단위계획을 조회한다."""
    g = next((x for x in DB["regulations"] if x["area"] == dong), None)
    if not g:
        return {"error": "등록된 지역의 규제 정보가 없습니다", "dong": dong}
    return {k: g[k] for k in ["reg_id", "area", "gu", "zone", "far", "lcr",
                              "is_permit_zone", "land_tx_note", "includes_district_plan"]}


def escalate_to_agent(reason, context=None):
    """상담원에게 이관한다. 등록 정보 없음·확신도 미달·범위 밖일 때 호출한다."""
    return {"escalated": True, "reason": reason, "context": context or {},
            "message": "정확한 확인을 위해 상담원에게 연결해 드리겠습니다."}


def _live_snapshot():
    """실거래 스냅샷(서울시 공개자료). 없으면 빈 dict — 요청 경로는 네트워크를 타지 않는다."""
    p = Path(__file__).parent / "data" / "live_snapshot.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_live_price(dong=None, name=None):
    """스냅샷에서 동의 실거래를 조회한다. 목 DB에 없는 동을 물었을 때 쓴다."""
    snap = _live_snapshot()
    if dong:
        rows = (snap.get("dongs") or {}).get(dong, [])
        if name:
            rows = [r for r in rows if name.replace(" ", "") in r["name"].replace(" ", "")]
        return {"dong": dong, "source": snap.get("source", ""), "fetched_at": snap.get("fetched_at", ""),
                "trades": rows, "count": len(rows)}
    return {"error": "동 이름을 지정해 주세요"}


TOOLS = {f.__name__: f for f in [search_property, get_auction_detail, get_real_price,
                                 get_redevelop_info, get_regulation_info, get_live_price,
                                 escalate_to_agent]}

DONGS = ["역삼동", "잠실동", "합정동", "개포동", "흑석동"] + sorted(
    (_live_snapshot().get("dongs") or {}).keys())  # 스냅샷 동(응암동 등)도 인식


def find_case_no(text):
    m = re.search(r"\d+타경\d+", text.replace(" ", ""))
    return m.group(0) if m else None


def find_dong(text):
    return next((d for d in DONGS if d in text), None)


def find_redev_name(text):
    """구역명 약칭(개포주공→개포주공1단지)도 찾는다. #2"""
    flat = text.replace(" ", "")
    for d in DB["redevelop"]:
        name = d["name"].replace(" ", "")
        if name in flat or (len(name) > 3 and name[:-3] in flat):
            return d["name"]
    return None


def find_price_name(text):
    flat = text.replace(" ", "")
    return next((p["name"] for p in DB["prices"] if p["name"].replace(" ", "") in flat), None)

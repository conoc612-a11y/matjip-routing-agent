# -*- coding: utf-8 -*-
"""실거래가 시범 연결. 서울시 열린데이터광장 API → 스냅샷 저장.

요청 경로(tools.get_live_price)는 네트워크를 타지 않고
data/live_snapshot.json만 읽는다. 갱신은 아래 fetch_snapshot()을 직접 돌린다.
재현 측정용이다: 평가時は 스냅샷 값이 gold가 된다.

    $env:SEOUL_OPEN_KEY="..."   # 또는 .env (저장소 업로드 금지)
    python live_price.py 개포동 흑석동
"""
import io
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
SNAP = BASE / "data" / "live_snapshot.json"
SALE_SVC = "tbLnOpendataRtmsV"   # 연립다세대 매매 실거래 (서울시)


def load_key():
    """환경변수 → .env 순으로 찾는다. 값은 절대 출력하지 않는다."""
    key = os.environ.get("SEOUL_OPEN_KEY", "")
    envf = BASE / ".env"
    if not key and envf.exists():
        for l in envf.read_text(encoding="utf-8").splitlines():
            if l.strip().startswith("SEOUL_OPEN_KEY"):
                key = l.split("=", 1)[1].strip().strip("'").strip('"')
    if not key:
        raise SystemExit("SEOUL_OPEN_KEY가 없습니다 (.env에 저장, 저장소 업로드 금지)")
    return key


def fetch_dong(dong, limit=50, want=10, max_pages=6, per_page=1000):
    """한 동의 최근 매매를 가져와 정규화한다. 금액 단위: 만원→원.
    서버 필터가 안 먹어 페이지 스캔으로 모은다 (want건 찰 때까지)."""
    key = load_key()
    import urllib.parse
    filt = "/STDG_NM/" + urllib.parse.quote(dong)
    got = []
    for suffix in [f"1/{limit}{filt}"]:
        url = f"http://openapi.seoul.go.kr:8088/{key}/json/{SALE_SVC}/{suffix}"
        with urllib.request.urlopen(url, timeout=30) as r:
            body = json.loads(r.read())
        if SALE_SVC in body:
            rows = body[SALE_SVC].get("row", [])
            if rows and all(x.get("STDG_NM") == dong for x in rows):
                got = rows
                break
    if not got:  # 필터 미지원 → 페이지 스캔
        for p in range(max_pages):
            url = (f"http://openapi.seoul.go.kr:8088/{key}/json/{SALE_SVC}/"
                   f"{p * per_page + 1}/{(p + 1) * per_page}/")
            with urllib.request.urlopen(url, timeout=30) as r:
                rows = json.loads(r.read())[SALE_SVC].get("row", [])
            got += [x for x in rows if x.get("STDG_NM") == dong]
            if len(got) >= want or not rows:
                break
    out = []
    for x in got:
        day = x.get("CTRT_DAY", "")
        out.append({
            "dong": dong, "gu": x.get("CGG_NM", ""),
            "name": x.get("BLDG_NM") or "(건물명 없음)",
            "date": f"{day[:4]}-{day[4:6]}" if len(day) == 8 else day,
            "price": int(str(x.get("THING_AMT", "0")).replace(",", "")) * 10000,
            "area_m2": x.get("ARCH_AREA"), "floor": x.get("FLR"),
            "use": x.get("BLDG_USG", ""),
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def fetch_snapshot(dongs):
    snap = {"fetched_at": str(date.today()), "source": "서울시 열린데이터광장 " + SALE_SVC,
            "unit_note": "price 단위: 원", "dongs": {}}
    explicit = [d for d in dongs if not d.startswith("+")]
    if "+" in "".join(dongs):  # "+N": 최근 페이지에서 목 DB에 없는 동 N개를 자동 발굴
        n = int(next(d for d in dongs if d.startswith("+"))[1:])
        explicit += discover_dongs(n)
    for dong in explicit:
        rows = fetch_dong(dong)
        snap["dongs"][dong] = rows[:10]  # 동별 최신 10건만 보관
        print(f"{dong}: {len(rows)}건 중 최신 {min(10, len(rows))}건 저장")
    SNAP.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", SNAP)


def discover_dongs(n, pages=3, per_page=1000):
    """최근 페이지에서 목 DB(역삼·잠실·합정)에 없는 동을 찾는다."""
    from collections import Counter
    key = load_key()
    cnt = Counter()
    seen = {}
    for p in range(pages):
        url = (f"http://openapi.seoul.go.kr:8088/{key}/json/{SALE_SVC}/"
               f"{p * per_page + 1}/{(p + 1) * per_page}/")
        with urllib.request.urlopen(url, timeout=30) as r:
            rows = json.loads(r.read())[SALE_SVC].get("row", [])
        for x in rows:
            dong = x.get("STDG_NM", "")
            if dong and dong not in ("역삼동", "잠실동", "합정동"):
                cnt[dong] += 1
                seen.setdefault(dong, x)
    picks = [d for d, _ in cnt.most_common() if cnt[d] >= 2][:n]
    print("발굴:", [(d, cnt[d]) for d in picks])
    return picks


if __name__ == "__main__":
    fetch_snapshot(sys.argv[1:] or ["+2"])

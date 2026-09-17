# -*- coding: utf-8 -*-
"""두 지표를 잰다. `python evaluate.py` [--only router|answer]

① 카테고리 판정 정확도 — eval_set.csv split=="eval" 10건
② 도구 호출 적절성 — 기대 도구 집합과 정확히 일치하면 1점
③ 답변 적절성 — must 전부 포함 + forbid 전부 미포함이면 1점
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = Path(__file__).parent


def load_eval_set():
    with open(BASE / "data" / "eval_set.csv", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["split"] == "eval"]


def load_gold():
    return json.loads((BASE / "data" / "answer_gold.json").read_text(encoding="utf-8"))


def norm_num(s):
    return re.sub(r"(?<=\d),(?=\d)", "", str(s))


def score_answer(expect, answer_text, tools_called, action):
    """한 턴 채점. 반환: (통과 여부, 실패 목록)."""
    fails = []
    a = norm_num(answer_text)
    if expect["action"] != action:
        fails.append(f'action: 기대 {expect["action"]} != 실제 {action}')
    need = set(expect.get("tools", []))
    if need != set(tools_called):
        fails.append(f'tools: 기대 {sorted(need)} != 실제 {sorted(tools_called)}')
    for m in expect.get("must", []):
        if norm_num(m) not in a:
            fails.append(f'must 누락: "{m}"')
    for fb in expect.get("forbid", []):
        if norm_num(fb) in a:
            fails.append(f'forbid 위반: "{fb}"')
    return (not fails), fails


def eval_router(report=True):
    from agent import classify
    rows = load_eval_set()
    pred = [classify(r["question"])["route"] for r in rows]
    gold = [r["route"] for r in rows]
    labels = sorted(set(gold))  # 이 평가셋의 카테고리 (5개)
    acc = sum(p == g for p, g in zip(pred, gold)) / len(rows)
    # macro F1 + 혼동 행렬 (stdlib만 사용)
    f1s, cm = [], {g: {p: 0 for p in labels} for g in labels}
    for g, p in zip(gold, pred):
        cm[g][p] += 1
    for lb in labels:
        tp = cm[lb][lb]
        fp = sum(cm[g][lb] for g in labels) - tp
        fn = sum(cm[lb][p] for p in labels) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    macro_f1 = sum(f1s) / len(f1s)
    print(f"-- 1. 카테고리 판정 ---- n={len(rows)}  정확도 {acc:.3f}  macro F1 {macro_f1:.3f}")
    if report:
        for r, p in zip(rows, pred):
            mark = "OK" if p == r["route"] else f"FAIL(예측 {p})"
            print(f"  {mark} [{r['qid']} {r['route']}] {r['question']}")
        print("\n  [혼동 행렬] 행=정답, 열=예측")
        print("  " + " ".join(f"{lb:>12}" for lb in ["정답\\예측"] + labels))
        for g in labels:
            print("  " + " ".join(f"{v:>12}" for v in [g] + [str(cm[g][p]) for p in labels]))
    return {"acc": acc, "macro_f1": macro_f1}


def eval_answer(report=True):
    from agent import customer_agent
    gold = {g["qid"]: g for g in load_gold()}
    rows = load_eval_set()

    # 채점기 자기 검증 — 모범 답안은 전부 통과해야 한다
    bad = [qid for qid, g in gold.items()
           if not score_answer(g["expect"], g["expect"]["reference"],
                               g["expect"].get("tools", []), g["expect"]["action"])[0]]
    print(f"-- 2/3. 도구·답변 ---- [채점기 자기 검증] 모범 답안 {len(gold)}건 중 실패 {len(bad)}건"
          + (f" {bad} [NG]" if bad else " [OK]"))

    outs, fails_all = [], []
    for r in rows:
        out = customer_agent(r["question"])
        ok, fails = score_answer(gold[r["qid"]]["expect"], out["answer"], out["tools"], out["action"])
        outs.append({**out, "qid": r["qid"], "ok": ok, "fails": fails})
        fails_all.extend(fails)
    tool_rate = sum(set(gold[o["qid"]]["expect"].get("tools", [])) == set(o["tools"]) for o in outs) / len(outs)
    ans_rate = sum(o["ok"] for o in outs) / len(outs)
    print(f"채점 {len(outs)}건 / 도구 적절성 {100 * tool_rate:.1f}% / 답변 적절성 {100 * ans_rate:.1f}%")
    if report:
        print("\n[실패 사례]")
        for o in outs:
            if not o["ok"]:
                print(f"  {o['qid']} 기대={gold[o['qid']]['expect']['action']} 실제={o['action']}  {'; '.join(o['fails'])}")
                print(f"      답변: {o['answer'][:90]}")
        if all(o["ok"] for o in outs):
            print("  없음 — 전건 통과")
    return {"tool_rate": tool_rate, "ans_rate": ans_rate}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["router", "answer"])
    args = ap.parse_args()
    out = {}
    if args.only != "answer":
        out["router"] = eval_router()
        print()
    if args.only != "router":
        out["answer"] = eval_answer()
    print("\n== 요약 ==")
    if "router" in out:
        print(f"  1. 카테고리 판정  정확도 {out['router']['acc']:.3f} · macro F1 {out['router']['macro_f1']:.3f}")
    if "answer" in out:
        print(f"  2. 도구 적절성    {100 * out['answer']['tool_rate']:.1f}%")
        print(f"  3. 답변 적절성    {100 * out['answer']['ans_rate']:.1f}%")

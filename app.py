# -*- coding: utf-8 -*-
"""데모 화면. `python app.py` → http://localhost:8000

stdlib http.server만 쓴다 (추가 설치 없이 실행).
답변과 함께 카테고리·호출한 도구·근거 규정 조각·검증 결과를 보여준다.
"""
import html
import io
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from agent import customer_agent
from context import build_context

EXAMPLES = ["2025타경12345 최저매각가격 얼마야?", "잠실동 시세 알려줘",
            "개포주공1단지 재건축 어디까지 진행됐어?", "역삼동 123-4 용도지역이랑 용적률 알려줘?",
            "역삼동 맛집 추천해줘"]

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>부동산 안내 라우팅 에이전트 데모</title>
<style>body{{font-family:sans-serif;max-width:760px;margin:2em auto;padding:0 1em}}
.card{{border:1px solid #ccc;border-radius:8px;padding:1em;margin:1em 0}}
.meta{{color:#555;font-size:.9em}}pre{{white-space:pre-wrap;background:#f6f6f6;padding:.8em}}
.tag{{display:inline-block;background:#eef;border-radius:4px;padding:0 .4em;margin:.1em}}</style>
</head><body>
<h1>부동산 안내 라우팅 에이전트</h1>
<form method="post"><input name="q" size="60" value="{q}" autofocus>
<button>문의</button></form>
<p class="meta">예시: {examples}</p>
{body}
</body></html>"""


def render(out):
    h = html.escape
    tools = " ".join(f'<span class="tag">{h(t)}</span>' for t in out["tools"]) or "(없음)"
    ctx = h(build_context(out["route"])) if out.get("route") != "OUT_OF_SCOPE" else "(범위 밖 — 규정 7장만 사용)"
    res = h(str(out["results"])[:1200])
    return (f'<div class="card"><p><b>상담원 &gt;</b> {h(out["answer"]).replace(chr(10), "<br>")}</p>'
            f'<p class="meta">route={h(str(out.get("route")))} conf={out.get("confidence")} '
            f'action={h(str(out.get("action")))} guardrail={out.get("guardrail_ok")}</p>'
            f'<p>호출한 도구: {tools}</p>'
            f'<p class="meta">근거 규정 조각:</p><pre>{ctx[:1500]}</pre>'
            f'<p class="meta">조회 결과:</p><pre>{res}</pre></div>')


class Handler(BaseHTTPRequestHandler):
    def _send(self, body):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        ex = " · ".join(f'<a href="/?q={urllib.parse.quote(e)}">{html.escape(e)}</a>' for e in EXAMPLES)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
        body = render(customer_agent(q)) if q else ""
        self._send(PAGE.format(q=html.escape(q), examples=ex, body=body))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        q = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8")).get("q", [""])[0]
        self.send_response(303)
        self.send_header("Location", "/?q=" + urllib.parse.quote(q))
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("http://localhost:8000 에서 데모 실행 중 (끝내려면 Ctrl+C)")
    HTTPServer(("localhost", 8000), Handler).serve_forever()

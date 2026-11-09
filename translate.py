"""영문 기사 제목·요약을 한국어로 번역한다.

엔진 선택 (환경 변수 TRANSLATOR):
  - "google" (기본): 구글 번역 공개 주소. 키·설치 불필요.
  - "claude": Claude API. `pip install anthropic` 후 ANTHROPIC_API_KEY 설정 필요.
    고유명사(작가·기관·전시명)를 더 정확히 다룬다.
  - "off": 번역하지 않음.
TRANSLATOR 를 지정하지 않아도 anthropic 패키지와 ANTHROPIC_API_KEY 가 있으면 claude 를 쓴다.
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
import urllib.request

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_BATCH = 40
CLAUDE_SYSTEM = (
    "You translate English art-world news headlines and summaries into natural, concise Korean "
    "for a Korean art news digest. Use the established Korean names of artists, institutions, "
    "exhibitions and publications; keep the original spelling when no Korean form is in common use. "
    "Do not add or omit information. "
    'Reply with only a JSON array of objects: {"id": ..., "title": ..., "summary": ...}, '
    "one per input item, in the same order. Leave summary empty if the input summary is empty."
)


def engine():
    choice = os.environ.get("TRANSLATOR", "").lower()
    if choice:
        return choice
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
            return "claude"
        except ImportError:
            pass
    return "google"


def _google_one(text):
    q = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": text})
    req = urllib.request.Request(f"{GOOGLE_URL}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _google(item):
    # 제목과 요약을 줄바꿈으로 묶어 한 번에 요청한다
    text = item["title"] if not item["summary"] else f"{item['title']}\n{item['summary']}"
    out = _google_one(text).split("\n", 1)
    return out[0].strip(), (out[1].strip() if len(out) > 1 and item["summary"] else "")


def _claude_batch(batch):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude 응답 중단: {resp.stop_reason}")
    text = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\[[\s\S]*\]", text)
    rows = json.loads(m.group(0)) if m else []
    return {r["id"]: (r.get("title", "").strip(), r.get("summary", "").strip()) for r in rows if r.get("id")}


def translate(items):
    """items: [{"id", "title", "summary"}] → {id: (title_ko, summary_ko)}. 실패한 항목은 빠진다."""
    eng = engine()
    if eng == "off" or not items:
        return {}
    print(f"  번역 {len(items)}건 ({eng})")
    result = {}
    if eng == "claude":
        for i in range(0, len(items), CLAUDE_BATCH):
            batch = [{k: it[k] for k in ("id", "title", "summary")} for it in items[i:i + CLAUDE_BATCH]]
            try:
                result.update(_claude_batch(batch))
            except Exception as e:  # 다음 수집 때 다시 시도
                print(f"  ✗ 번역 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return result
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_google, it): it["id"] for it in items}
        for fut in cf.as_completed(futures):
            try:
                result[futures[fut]] = fut.result()
            except Exception as e:
                print(f"  ✗ 번역 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return result

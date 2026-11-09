"""예술계 뉴스 수집기.

sources.json 에 등록된 신뢰 매체에서 기사를 모아 site/data/news.js 로 저장한다.
외부 라이브러리 없이 Python 표준 라이브러리만 사용한다.

    python collect.py
"""
import concurrent.futures as cf
import email.utils
import hashlib
import html
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import exhibitions
from translate import translate

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources.json"
DATA_DIR = ROOT / "site" / "data"
STATE = DATA_DIR / "archive.json"
KEEP_DAYS = 30
MAX_PER_SOURCE = 40
SUMMARY_LEN = 220
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; ArtNewsDigest/1.0)"

# 분야 분류: 위에서부터 먼저 걸리는 분류를 사용
CATEGORIES = [
    ("시장·경매", r"경매|옥션|낙찰|아트페어|키아프|프리즈|미술시장|auction|sotheby|christie|phillips|art fair|art market|\bsale[sd]?\b|price|record|dealer|frieze|art basel"),
    ("부고·인물", r"별세|타계|부고|추모|수상|인터뷰|dies|died|dead at|obituary|remember|award|prize|winner|interview|in conversation"),
    ("기관·정책", r"관장|임명|선임|사임|정책|예산|지원사업|공모|문체부|재단|위원회|director|appoint|named|resign|funding|grant|policy|government|lawsuit|court|restitution|repatriat|union|strike|layoff"),
    ("공연", r"공연|오페라|발레|무용|안무|클래식|오케스트라|교향악|연극|뮤지컬|국악|리사이틀|피아니스트|바이올린|지휘자|콩쿠르|음악회|opera|ballet|dance|theatre|theater|orchestra|symphony|concert|recital"),
    ("문화유산", r"국가유산|문화재|문화유산|유물|유적|발굴|고궁|창덕궁|경복궁|덕수궁|사찰|불상|heritage|archaeolog|antiquit|excavat|looted"),
    ("전시", r"전시|개인전|단체전|회고전|기획전|특별전|비엔날레|트리엔날레|미술관|개막|exhibition|show|retrospective|biennale|biennial|on view|opens|survey"),
    ("비평·리뷰", r"리뷰|비평|평론|칼럼|review|critic|essay|opinion|column"),
]
CATEGORY_RES = [(name, re.compile(p, re.I)) for name, p in CATEGORIES]


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            charset = r.headers.get_content_charset()
    except urllib.error.URLError as e:
        # 인증서 체인이 불완전한 일부 국내 사이트 대응
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                charset = r.headers.get_content_charset()
        else:
            raise
    if not charset:
        m = re.search(rb'encoding=["\']([\w-]+)', raw[:300]) or re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
        charset = m.group(1).decode() if m else "utf-8"
    return raw.decode(charset, "replace")


def strip_html(s):
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", s).strip()


def shorten(s, n=SUMMARY_LEN):
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,.·") + "…"


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        t = s.replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d"):
            try:
                d = datetime.fromisoformat(t) if fmt is None else datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def find_image(el, *html_blobs):
    for c in el.iter():
        name = local(c.tag)
        if name in ("content", "thumbnail") and c.get("url") and (
            "image" in (c.get("type") or c.get("medium") or "image")
        ):
            return c.get("url")
        if name == "enclosure" and (c.get("type") or "").startswith("image") and c.get("url"):
            return c.get("url")
    for blob in html_blobs:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', blob or "")
        if m:
            return html.unescape(m.group(1))
    return ""


def parse_feed(text):
    text = text.lstrip("﻿ \r\n")
    text = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
    root = ET.fromstring(text.encode("utf-8"))
    items = []
    for el in root.iter():
        name = local(el.tag)
        if name not in ("item", "entry"):
            continue
        link = child_text(el, "link")
        if not link:
            for c in el:
                if local(c.tag) == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    link = c.get("href")
                    break
        desc = child_text(el, "description", "summary")
        content = ""
        for c in el:
            if local(c.tag) in ("encoded", "content") and (c.text or "").strip():
                content = c.text
                break
        cats = [strip_html(c.text or c.get("term") or "") for c in el if local(c.tag) == "category"]
        items.append({
            "title": strip_html(child_text(el, "title")),
            "url": link.strip(),
            "date": parse_date(child_text(el, "pubdate", "published", "updated", "date", "issued")),
            # 워드프레스 피드가 붙이는 "The post … appeared first on …" 문구 제거
            "summary": re.sub(r"\s*The post .{0,300}? appeared first on .{0,80}$", "", strip_html(desc or content)),
            "image": find_image(el, desc, content),
            "author": strip_html(child_text(el, "creator", "author") or ""),
            "tags": [c for c in cats if c][:5],
        })
    return items


def parse_html_list(text, src):
    items, seen = [], set()
    # 들여쓰기 공백이 많은 페이지가 있어 공백을 한 칸으로 줄인 뒤 매칭한다
    text = re.sub(r"\s+", " ", text)
    for m in re.finditer(src["pattern"], text):
        g = m.groupdict()
        url = html.unescape(g.get("url") or m.group(1))
        if src.get("link"):
            url = src["link"].format(url=url)
        url = urllib.parse.urljoin(src["list"], url)
        title = strip_html(g.get("title") or m.group(2))
        if g.get("org"):
            title = f"{strip_html(g['org'])}: {title}"
        if url in seen or not title:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "date": parse_date(g.get("date")),
                      "summary": strip_html(g.get("summary") or ""), "image": "", "author": "", "tags": []})
    return items


def parse_json_list(text, src):
    """JSON 응답의 목록에서 기사를 만든다. src["fields"] 는 {title, date, url, summary} → JSON 키."""
    f = src["fields"]
    items = []
    for row in json.loads(text).get(src["json_list"], []):
        url = src["link"].format(url=row.get(f["url"], ""))
        items.append({
            "title": strip_html(str(row.get(f["title"], ""))),
            "url": urllib.parse.urljoin(src["list"], url),
            "date": parse_date(row.get(f["date"])),
            "summary": strip_html(str(row.get(f.get("summary", ""), "") or "")),
            "image": "", "author": "", "tags": [],
        })
    return items


def categorize(item):
    blob = f"{item['title']} {' '.join(item['tags'])} {item['summary'][:120]}"
    for name, rx in CATEGORY_RES:
        if rx.search(blob):
            return name
    return "일반"


def collect_source(src):
    text = fetch(src["feed"] if src["type"] == "rss" else src["list"])
    parser = {"rss": parse_feed, "html": lambda t: parse_html_list(t, src), "json": lambda t: parse_json_list(t, src)}
    items = parser[src["type"]](text)
    if src.get("url_contains"):  # 사이트 전체 피드에서 예술 섹션만 남긴다
        items = [it for it in items if src["url_contains"] in it["url"]]
    return [it for it in items if it["title"] and it["url"].startswith("http")][:MAX_PER_SOURCE]


def item_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def norm_title(t):
    return re.sub(r"[\W_]+", "", t.lower())


# ── 주요 이슈: 같은 소식을 여러 매체가 다루면 하나의 이슈로 묶는다 ──
ISSUE_DAYS = 3
ISSUE_SKIP = {"eflux"}  # 기관 공지는 매번 고유한 발표라 묶기에서 제외
# 흔해서 같은 사건의 근거가 되지 못하는 단어
ISSUE_STOP = set("""the a an and or of to in on for with at by from as is are was were be been its it this that these those
new news art artist artists museum gallery show exhibition exhibitions work works after over into about more than
his her their our your who what when where how why will has have had not but year years first says said week
plus also just one two three most latest world fine arts centre center court french venice biennale biennial
pavilion contemporary foundation national award awards prize january february march april may june july august
september october november december""".split())


def _words(text):
    return [re.sub(r"['’]s$", "", w) for w in re.findall(r"[A-Za-z][A-Za-z'’\-]+", text)]


def _issue_tokens(it):
    tw = _words(it["title"])
    proper = {w.lower() for w in tw if w[0].isupper() and w.lower() not in ISSUE_STOP and len(w) > 2}
    title_w = {w.lower() for w in tw if w.lower() not in ISSUE_STOP and len(w) > 3}
    body_w = {w.lower() for w in _words(f"{it['title']} {it['summary'][:160]}") if w.lower() not in ISSUE_STOP and len(w) > 3}
    return proper, title_w, body_w


def _jaccard(a, b):
    return len(a & b) / max(1, len(a | b))


def find_issues(items, now):
    """여러 매체가 함께 보도한 기사 묶음을 보도 매체 수가 많은 순으로 돌려준다.

    제목에 같은 고유명사가 있고 본문 단어가 충분히 겹치거나, 제목 자체가 많이 겹칠 때만 묶는다.
    (요약만 보면 여러 소식을 나열하는 일일 브리핑 기사가 엉뚱하게 묶인다)
    """
    since = now - timedelta(days=ISSUE_DAYS)
    pool = [it for it in items if it["source"] not in ISSUE_SKIP and datetime.fromisoformat(it["date"]) >= since]
    toks = [_issue_tokens(it) for it in pool]
    parent = list(range(len(pool)))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(pool)):
        for b in range(a + 1, len(pool)):
            if pool[a]["source"] == pool[b]["source"]:
                continue
            (pa, ta, ba), (pb, tb, bb) = toks[a], toks[b]
            if (pa & pb and _jaccard(ba, bb) >= 0.15) or _jaccard(ta, tb) >= 0.4:
                parent[root(a)] = root(b)

    groups = {}
    for i, it in enumerate(pool):
        groups.setdefault(root(i), []).append(it)
    issues = []
    for members in groups.values():
        sources = list(dict.fromkeys(it["source"] for it in members))
        if len(sources) < 2:
            continue
        members.sort(key=lambda it: it["date"], reverse=True)
        # 대표 기사: 이미지와 요약이 있는 기사 우선, 그다음 최신
        lead = max(members, key=lambda it: (bool(it["image"]), bool(it["summary"]), it["date"]))
        issues.append({"lead": lead["id"], "items": [it["id"] for it in members], "sources": sources,
                       "latest": members[0]["date"]})
    issues.sort(key=lambda x: (len(x["sources"]), x["latest"]), reverse=True)
    return issues


def main():
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    now = datetime.now(timezone.utc)
    status = {}

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(collect_source, s): s for s in sources}
        for fut in cf.as_completed(futures):
            src = futures[fut]
            try:
                items = fut.result()
            except Exception as e:  # 한 매체가 실패해도 나머지는 계속
                status[src["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "count": 0}
                print(f"  ✗ {src['name']}: {e}", file=sys.stderr)
                continue
            new = 0
            for it in items:
                iid = item_id(it["url"])
                prev = archive.get(iid)
                date = it["date"] or (datetime.fromisoformat(prev["date"]) if prev else now)
                if date > now + timedelta(hours=1):
                    date = now
                if not prev:
                    new += 1
                entry = {
                    "id": iid,
                    "source": src["id"],
                    "title": it["title"],
                    "url": it["url"],
                    "date": date.isoformat(),
                    "summary": shorten(it["summary"]),
                    "image": it["image"],
                    "author": it["author"],
                    "category": categorize(it),
                }
                # 원문이 그대로면 지난번 번역을 재사용
                if prev and prev.get("title_ko") and (prev["title"], prev["summary"]) == (entry["title"], entry["summary"]):
                    entry["title_ko"], entry["summary_ko"] = prev["title_ko"], prev.get("summary_ko", "")
                archive[iid] = entry
            dates = [it["date"] for it in items if it["date"]]
            latest = max(dates).isoformat() if dates else None
            status[src["id"]] = {"ok": True, "count": len(items), "new": new, "latest": latest}
            print(f"  ✓ {src['name']}: {len(items)}건 (신규 {new})")

    cutoff = now - timedelta(days=KEEP_DAYS)
    archive = {k: v for k, v in archive.items() if datetime.fromisoformat(v["date"]) >= cutoff}

    # 한국어가 아닌 매체의 기사 중 아직 번역되지 않은 것만 번역
    langs = {s["id"]: s.get("lang", "en") for s in sources}
    todo = [v for v in archive.values() if langs.get(v["source"], "en") != "ko" and not v.get("title_ko")]
    for iid, (title_ko, summary_ko) in translate(todo).items():
        if title_ko:
            archive[iid]["title_ko"], archive[iid]["summary_ko"] = title_ko, summary_ko

    # 같은 매체 안에서 제목이 같은 중복 기사 제거
    seen, items = set(), []
    for it in sorted(archive.values(), key=lambda x: x["date"], reverse=True):
        key = (it["source"], norm_title(it["title"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(it)

    STATE.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    public_sources = [
        {k: s[k] for k in ("id", "name", "region", "lang", "kind", "home", "why")} | {"status": status.get(s["id"], {})}
        for s in sources
    ]
    issues = find_issues(items, now)
    payload = {"updated": now.isoformat(), "sources": public_sources, "issues": issues, "items": items}
    body = json.dumps(payload, ensure_ascii=False)
    (DATA_DIR / "news.json").write_text(body, encoding="utf-8")
    # file:// 로 바로 열어도 동작하도록 JS 버전도 함께 저장
    (DATA_DIR / "news.js").write_text(f"window.ART_NEWS = {body};\n", encoding="utf-8")
    ok = sum(1 for s in status.values() if s["ok"])
    print(f"\n완료: 매체 {ok}/{len(sources)} 성공, 기사 {len(items)}건, 주요 이슈 {len(issues)}건 → {DATA_DIR / 'news.js'}")

    print("\n전시 수집")
    try:
        exhibitions.main()
    except Exception as e:  # 전시 수집이 실패해도 뉴스 데이터는 이미 저장됨
        print(f"  ✗ 전시 수집 실패: {type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

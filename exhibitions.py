"""국내 미술관·박물관 전시 수집기.

각 기관 홈페이지의 전시 목록에서 현재·예정 전시를 모아 site/data/exhibitions.js 로 저장한다.
collect.py 가 뉴스 수집 뒤에 함께 호출하며, 단독으로도 실행할 수 있다.

    python exhibitions.py
"""
import concurrent.futures as cf
import hashlib
import html
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "site" / "data"
OUT_JSON = DATA_DIR / "exhibitions.json"
KST = timezone(timedelta(hours=9))
UPCOMING_DAYS = 120  # 이 기간 안에 개막하는 전시까지 보여준다
UA = "Mozilla/5.0 (compatible; ArtNewsDigest/1.0)"

# 목록 페이지에서 정규식으로 전시를 뽑는 기관들.
# 이름 있는 그룹: url, title, start, end (필수) / img, place, branch, alt (선택)
SITES = [
    {
        "id": "sema", "name": "서울시립미술관", "city": "서울", "home": "https://sema.seoul.go.kr",
        "list": "https://sema.seoul.go.kr/",
        # 홈페이지 전시 슬라이드: 링크·이미지·제목 뒤에 분관명과 기간이 hidden input 으로 붙어 있다
        "pattern": r'<a class="img-wrap" href="(?P<url>/kr/whatson/exhibition/detail\?exNo=\d+)">\s*'
                   r'<img src="(?P<img>[^"]+)" alt="(?P<title>[^"]*)">\s*</a>[\s\S]{0,300}?'
                   r'name="bannerTitle" value="(?P<branch>[^"]*)"\s*/>\s*'
                   r'<input type="hidden" name="bannerDate" value="(?P<start>[\d/]+)~(?P<end>[\d/]+)"',
    },
    {
        "id": "nmk", "name": "국립중앙박물관", "city": "서울", "home": "https://www.museum.go.kr",
        "list": "https://www.museum.go.kr/site/main/exhiSpecialTheme/list/current",
        "pattern": r'<img src="(?P<img>/afile/previewThumbnail/[^"]+)"[^>]*>\s*</a>[\s\S]{0,600}?'
                   r'<a href="(?P<url>\?schM=view[^"]+)">\s*<strong>(?P<title>[^<]+)</strong>[\s\S]{0,300}?'
                   r'<p>(?P<start>[\d-]+)~(?P<end>[\d-]+)</p>[\s\S]{0,200}?장소</strong>\s*<p>(?P<place>[^<]*)</p>',
    },
    {
        "id": "daegu", "name": "대구미술관", "city": "대구", "home": "https://daeguartmuseum.or.kr",
        "list": "https://daeguartmuseum.or.kr/index.do?menu_id=00000729",
        # 상세 페이지는 자바스크립트로 이동하므로 전시 ID 로 주소를 만든다
        "link": "/index.do?menu_id=00000729&menu_link=/front/ehi/ehiViewFront.do&ehi_id={url}",
        "pattern": r"fnView\('(?P<url>EHI_\d+)'\)[\s\S]{0,300}?<img src='(?P<img>[^']+)'[\s\S]{0,500}?"
                   r'<span class="item_tit">(?P<title>[^<]+)</span>[\s\S]{0,200}?'
                   r'info_date">(?P<start>[\d-]+)~(?P<end>[\d-]+)</span>[\s\S]{0,200}?장소</em><span>(?P<place>[^<]*)</span>',
    },
    {
        "id": "gmoma", "name": "경기도미술관", "city": "경기", "home": "https://gmoma.ggcf.kr",
        "list": "https://gmoma.ggcf.kr/exhibitions",
        "pattern": r'<a href="(?P<url>/exhibitions/\d+)">\s*<div class="img-box">\s*<img src="(?P<img>[^"]+)"[^>]*>\s*</div>\s*'
                   r'<p>(?P<title>[^<]+)</p>\s*<p class="date">\s*(?P<start>[\d. ]+?)\s*—\s*(?P<end>[\d. ]+?)\s*</p>',
    },
    {
        "id": "busan", "name": "부산시립미술관", "city": "부산", "home": "https://art.busan.go.kr",
        "list": "https://art.busan.go.kr/tblTsite07Display/listNowClient.nm",
        "pattern": r'<a href="(?P<url>/tblTsite07Display/viewNowClient\.nm\?id=\d+)"> <img src="(?P<img>[^"]+)" alt="[^"]*"> </a> </div> '
                   r'<div class="infor"> <span class="tit"><a href="[^"]+">(?P<title>[^<]+)</a></span> '
                   r'<span class="date">(?P<start>[\d-]+) – (?P<end>[\d-]+)</span>',
    },
    {
        "id": "ulsan", "name": "울산시립미술관", "city": "울산", "home": "https://www.ulsan.go.kr/s/uam/main.ulsan",
        "list": "https://www.ulsan.go.kr/s/uam/main.ulsan",
        # 메인 슬라이드: 이미지 alt 에 기간이 있는 항목만 전시로 본다 (교육 프로그램 배너는 기간이 없다)
        "pattern": r'<img src="(?P<img>/s/cmm/fms/getImage\.do[^"]+)" alt="[^"]*?(?P<start>20\d\d\.\d{1,2}\.\d{1,2})\. ~ '
                   r'(?P<end>20\d\d\.\d{1,2}\.\d{1,2})\.[^"]*" /> <div class="visual_txtbox"> <div class="visual_txt"> '
                   r'<h2>(?P<title>[^<]+)</h2>[\s\S]{0,400}?<a href="(?P<url>[^"]+)"',
    },
    {
        "id": "jma", "name": "전북도립미술관", "city": "전북", "home": "https://www.jma.go.kr",
        "list": "https://www.jma.go.kr/",
        "pattern": r'<a href="(?P<url>https://www\.jma\.go\.kr/web/page\.php\?pcode=AA0\d&exhibit_code=\d+)"> '
                   r'<div class="img"><div class="layer"></div><img src="(?P<img>[^"]+)" alt="[^"]*" /></div> <dl> '
                   r'<dt>(?P<title>[^<]+)</dt> <dd>(?P<branch>[^|<]+?) \| (?P<start>[\d-]+) ~ (?P<end>[\d-]+)</dd>',
    },
]

# 전시 목록에 섞여 있는 안내성 게시물
NOT_EXHIBITION = re.compile(r"전시\s*일정|휴관|안내$")

MMCA = {"id": "mmca", "name": "국립현대미술관", "city": "서울", "home": "https://www.mmca.go.kr"}
MMCA_CITY = {"과천": "경기", "어린이미술관": "경기", "청주": "충북"}

INSTITUTIONS = [MMCA] + SITES


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
    # 일부 공공기관 사이트는 인증서 체인이 불완전하다
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", s or "")))).strip()


def norm_date(s):
    """'2026. 10. 29.', '2026/08/27', '2026.10.16', '2026-10-23' → '2026-10-29'"""
    nums = re.findall(r"\d+", s or "")
    if len(nums) < 3:
        return None
    y, m, d = (int(n) for n in nums[:3])
    return f"{y:04d}-{m:02d}-{d:02d}"


def collect_mmca():
    out = []
    base = MMCA["home"]
    for flag in (1, 2):  # 1 = 현재 전시, 2 = 예정 전시
        page = 1
        while True:
            q = urllib.parse.urlencode({"exhFlag": flag, "searchExhPlaCd": "", "searchExhCd": "", "sort": 1, "pageIndex": page})
            data = json.loads(fetch(f"{base}/exhibitions/AjaxExhibitionList.do?{q}"))
            for x in data.get("exhibitionsList", []):
                branch = x.get("exhPlaNm") or ""
                out.append({
                    "title": clean(x.get("exhTitle")),
                    "branch": branch,
                    "place": clean(x.get("exhPlaDtl")),
                    "start": x.get("exhStDt"), "end": x.get("exhEdDt"),
                    "url": f"{base}/exhibitions/exhibitionsDetail.do?exhFlag={flag}&exhId={x.get('exhId')}",
                    "image": base + x["exhThumbImg"] if x.get("exhThumbImg") else "",
                    "summary": clean(x.get("exhContentsSumm")),
                    "artists": clean(x.get("exhArtist")),
                    "city": MMCA_CITY.get(branch, "서울"),
                })
            pages = (data.get("paginationInfo") or {}).get("totalPageCount", 1)
            if page >= pages:
                break
            page += 1
    return out


def collect_site(site):
    # 들여쓰기 공백이 많은 페이지가 있어 공백을 한 칸으로 줄인 뒤 매칭한다
    text = re.sub(r"\s+", " ", fetch(site["list"]))
    if site.get("segment"):
        a, b = site["segment"]
        text = text[text.find(a):text.find(b)] if a in text and b in text else text
    out = []
    for m in re.finditer(site["pattern"], text):
        g = m.groupdict()
        url = html.unescape(g["url"])
        if site.get("link"):
            url = site["link"].format(url=url)
        out.append({
            "title": clean(g.get("alt") or g["title"]),
            "branch": clean(g.get("branch") or ""),
            "place": clean(g.get("place") or ""),
            "start": norm_date(g["start"]), "end": norm_date(g["end"]),
            "url": urllib.parse.urljoin(site["list"], url),
            # 파일명에 공백·한글이 들어간 이미지 주소가 있어 경로를 인코딩한다
            "image": urllib.parse.quote(urllib.parse.urljoin(site["list"], html.unescape(g["img"])), safe=":/?=&%#") if g.get("img") else "",
            "summary": "", "artists": "",
            "city": site["city"],
        })
    return out


def run_institution(inst):
    return collect_mmca() if inst["id"] == "mmca" else collect_site(inst)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prev = json.loads(OUT_JSON.read_text(encoding="utf-8")) if OUT_JSON.exists() else {"items": []}
    today = datetime.now(KST).date().isoformat()
    horizon = (datetime.now(KST) + timedelta(days=UPCOMING_DAYS)).date().isoformat()
    items, status = [], {}

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_institution, inst): inst for inst in INSTITUTIONS}
        for fut in cf.as_completed(futures):
            inst = futures[fut]
            try:
                got = fut.result()
                if not got:
                    raise ValueError("전시 항목을 찾지 못함 (페이지 구조 변경 가능성)")
            except Exception as e:
                # 실패한 기관은 지난번 수집 결과를 유지
                kept = [x for x in prev["items"] if x["museum"] == inst["id"]]
                items += kept
                status[inst["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "count": len(kept)}
                print(f"  ✗ 전시 {inst['name']}: {e}", file=sys.stderr)
                continue
            seen, n = set(), 0
            for x in got:
                if not (x["title"] and x["start"] and x["end"]) or x["url"] in seen or NOT_EXHIBITION.search(x["title"]):
                    continue
                if x["end"] < today or x["start"] > horizon:
                    continue
                seen.add(x["url"])
                x["museum"] = inst["id"]
                x["id"] = hashlib.sha1(x["url"].encode()).hexdigest()[:12]
                items.append(x)
                n += 1
            status[inst["id"]] = {"ok": True, "count": n}
            print(f"  ✓ 전시 {inst['name']}: {n}건")

    items = [x for x in items if x["end"] >= today]
    items.sort(key=lambda x: (x["start"], x["title"]))
    museums = [{"id": i["id"], "name": i["name"], "city": i["city"], "home": i["home"], "status": status.get(i["id"], {})}
               for i in INSTITUTIONS]
    payload = {"updated": datetime.now(timezone.utc).isoformat(), "museums": museums, "items": items}
    body = json.dumps(payload, ensure_ascii=False)
    OUT_JSON.write_text(body, encoding="utf-8")
    (DATA_DIR / "exhibitions.js").write_text(f"window.ART_EXHIBITIONS = {body};\n", encoding="utf-8")
    print(f"전시 {len(items)}건 → {DATA_DIR / 'exhibitions.js'}")


if __name__ == "__main__":
    main()

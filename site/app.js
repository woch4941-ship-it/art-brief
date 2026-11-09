(function () {
  const DATA = window.ART_NEWS || { items: [], sources: [], updated: null };
  const SRC = Object.fromEntries(DATA.sources.map((s) => [s.id, s]));
  const CATS = ["전시", "시장·경매", "기관·정책", "공연", "문화유산", "부고·인물", "비평·리뷰", "일반"];
  const PAGE = 40;

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };
  const RECENT_DAYS = 3;
  const state = { q: "", region: "all", range: RECENT_DAYS, source: "all", cat: "all", shown: PAGE, lang: store.get("lang") || "ko" };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // 번역 모드에서는 번역문을, 원문 모드나 번역이 없을 때는 원문을 보여준다
  const translated = (it) => state.lang === "ko" && it.title_ko;
  const T = (it) => (translated(it) ? it.title_ko : it.title);
  const S = (it) => (translated(it) ? it.summary_ko || it.summary : it.summary);
  const headline = (it) => `<h3>${link(it, esc(T(it)))}</h3>${translated(it) ? `<div class="orig" lang="en">${esc(it.title)}</div>` : ""}`;

  const fmtDay = new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" });
  const fmtFull = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" });
  function ago(d) {
    const m = Math.round((Date.now() - d) / 60000);
    if (m < 1) return "방금";
    if (m < 60) return `${m}분 전`;
    if (m < 60 * 24) return `${Math.floor(m / 60)}시간 전`;
    return `${Math.floor(m / 1440)}일 전`;
  }

  const items = DATA.items.map((it) => ({ ...it, d: new Date(it.date), s: SRC[it.source] || {} }));

  function filtered(ignoreCat) {
    const since = Date.now() - state.range * 86400000;
    const q = state.q.trim().toLowerCase();
    return items.filter((it) =>
      it.d >= since &&
      (state.region === "all" || it.s.region === state.region) &&
      (state.source === "all" || it.source === state.source) &&
      (ignoreCat || state.cat === "all" || it.category === state.cat) &&
      (!q || `${it.title} ${it.title_ko || ""} ${it.summary} ${it.summary_ko || ""} ${it.s.name}`.toLowerCase().includes(q))
    );
  }

  function byline(it) {
    return `<div class="byline"><span class="src">${esc(it.s.name)}</span>
      <span class="tag">${esc(it.category)}</span>
      <time datetime="${esc(it.date)}" title="${esc(fmtFull.format(it.d))}">${ago(it.d)}</time></div>`;
  }
  const img = (it) => it.image
    ? `<img class="thumb" src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.item')?.classList.add('noimg');this.remove()">`
    : "";
  const link = (it, inner) => `<a href="${esc(it.url)}" target="_blank" rel="noopener">${inner}</a>`;

  function renderChips() {
    const base = filtered(true);
    const count = (c) => base.filter((it) => it.category === c).length;
    $("#cats").innerHTML = [`<button data-v="all" class="${state.cat === "all" ? "on" : ""}">전체 <small>${base.length}</small></button>`]
      .concat(CATS.map((c) => `<button data-v="${c}" class="${state.cat === c ? "on" : ""}">${c} <small>${count(c)}</small></button>`))
      .join("");
  }

  const BY_ID = Object.fromEntries(items.map((it) => [it.id, it]));
  const NOTABLE_CATS = new Set(["기관·정책", "시장·경매", "부고·인물", "문화유산"]);
  const compact = (it) => `<article class="item ${it.image ? "" : "noimg"}"><div>${byline(it)}
      ${headline(it)}</div>${img(it)}</article>`;

  function renderIssues() {
    const issues = (DATA.issues || []).map((x) => ({ ...x, lead: BY_ID[x.lead], members: x.items.map((id) => BY_ID[id]).filter(Boolean) }))
      .filter((x) => x.lead);
    $("#issues").innerHTML = issues.length ? issues.map((x, n) => {
      const it = x.lead;
      const names = x.sources.map((id) => esc((SRC[id] || {}).name || id)).join(" · ");
      const pic = it.image ? link(it, `<img class="thumb" src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.issue').classList.add('noimg');this.parentNode.remove()">`) : "";
      const related = x.members.map((m) => `<li><span class="src">${esc(m.s.name)}</span> ${link(m, esc(T(m)))}</li>`).join("");
      return `<article class="issue ${n === 0 ? "issue--top" : ""} ${it.image ? "" : "noimg"}">
        ${pic}
        <div class="issue__body">
          <div class="issue__meta"><b class="count">${x.sources.length}개 매체 보도</b><span>${names}</span><span>${ago(new Date(x.latest))}</span></div>
          ${headline(it)}
          ${S(it) ? `<p>${esc(S(it))}</p>` : ""}
          <details><summary>관련 기사 ${x.members.length}건</summary><ul>${related}</ul></details>
        </div>
      </article>`;
    }).join("") : `<p class="empty">최근 ${RECENT_DAYS}일 동안 여러 매체가 함께 다룬 소식이 아직 없습니다.</p>`;

    const inIssue = new Set(issues.flatMap((x) => x.items));
    const since = Date.now() - RECENT_DAYS * 86400000;
    const used = new Set();
    const notable = items.filter((it) => {
      if (it.d < since || it.s.region !== "해외" || inIssue.has(it.id) || !NOTABLE_CATS.has(it.category) || used.has(it.source)) return false;
      used.add(it.source);
      return true;
    }).slice(0, 6);
    $("#notable").innerHTML = notable.map(compact).join("") || `<p class="empty">해당 소식이 없습니다.</p>`;

    const domestic = items.filter((it) => it.s.region === "국내").slice(0, 6);
    $("#domestic").innerHTML = domestic.map(compact).join("") || `<p class="empty">최근 국내 기사가 없습니다.</p>`;
  }

  function render() {
    renderChips();
    const list = filtered(false);
    const page = list.slice(0, state.shown);
    let html = "", day = "";
    for (const it of page) {
      const k = fmtDay.format(it.d);
      if (k !== day) { html += `${day ? "</div>" : ""}<div class="day"><h2>${k}</h2>`; day = k; }
      html += `<article class="item ${it.image ? "" : "noimg"}"><div>${byline(it)}
        ${headline(it)}${S(it) ? `<p>${esc(S(it))}</p>` : ""}</div>${img(it)}</article>`;
    }
    if (day) html += "</div>";
    if (list.length > state.shown) html += `<button class="more" id="more">더 보기 (${list.length - state.shown}건 남음)</button>`;
    $("#list").innerHTML = html;
    $("#empty").hidden = list.length > 0;
  }

  function renderMeta() {
    const ok = DATA.sources.filter((s) => s.status && s.status.ok).length;
    const upd = DATA.updated ? fmtFull.format(new Date(DATA.updated)) : "-";
    $("#meta").innerHTML = `<span>마지막 갱신 <b>${upd}</b></span><span>수집 매체 <b>${ok}/${DATA.sources.length}</b></span><span>최근 ${RECENT_DAYS}일 기사 <b>${items.filter((it) => it.d >= Date.now() - RECENT_DAYS * 86400000).length.toLocaleString()}건</b></span>`;
  }

  function renderSources() {
    const counts = {};
    const recent = Date.now() - RECENT_DAYS * 86400000;
    items.forEach((it) => { if (it.d >= recent) counts[it.source] = (counts[it.source] || 0) + 1; });
    const groups = [
      ["국내", (s) => s.region === "국내"],
      ["해외", (s) => s.region === "해외"],
    ];
    $("#source-table").innerHTML = groups.map(([title, fn]) => {
      const rows = DATA.sources.filter(fn).map((s) => {
        const st = s.status || {};
        // 피드가 살아 있어도 새 글이 60일 넘게 없으면 갱신이 멈춘 것으로 본다
        const stale = st.ok && st.latest && Date.now() - new Date(st.latest) > 60 * 86400000;
        const bad = !st.ok || stale;
        const stat = !st.ok ? "수집 실패" : stale ? "피드 갱신 중단 의심" : `최근 ${RECENT_DAYS}일 ${counts[s.id] || 0}건`;
        return `<div class="src-row"><a href="${esc(s.home)}" target="_blank" rel="noopener">${esc(s.name)}</a>
          <span class="why"><span class="tag">${esc(s.kind)}</span> ${esc(s.why)}</span><span class="st ${bad ? "bad" : ""}" title="${esc(st.error || "")}">${stat}</span></div>`;
      }).join("");
      return rows ? `<div class="src-group"><h3>${title}</h3>${rows}</div>` : "";
    }).join("");
  }

  // ── 전시 ──
  const EX = window.ART_EXHIBITIONS || { items: [], museums: [] };
  const MUSEUM = Object.fromEntries(EX.museums.map((m) => [m.id, m]));
  const exState = { q: "", city: "all", museum: "all" };
  const ENDING_DAYS = 14;
  const dayNum = (s) => Math.round(new Date(s + "T00:00:00") / 86400000);
  const todayNum = () => Math.round(new Date(new Date().toDateString()) / 86400000);
  // 종료일을 먼 미래(2090년 이후)로 둔 전시는 상설 전시로 본다
  const permanent = (x) => x.end >= "2090";
  const fmtRange = (x) => permanent(x) ? "상설 전시" : `${x.start.replaceAll("-", ".")} – ${x.end.replaceAll("-", ".")}`;

  function exCard(x, today) {
    const m = MUSEUM[x.museum] || {};
    const toEnd = dayNum(x.end) - today, toStart = dayNum(x.start) - today;
    const badge = toStart > 0 ? `<span class="badge badge--soon">개막 D-${toStart}</span>`
      : toEnd <= ENDING_DAYS ? `<span class="badge badge--end">${toEnd === 0 ? "오늘 종료" : `종료 D-${toEnd}`}</span>`
      : `<span class="badge">진행 중</span>`;
    const where = [m.name, x.branch].filter(Boolean).join(" · ");
    return `<a class="ex-card" href="${esc(x.url)}" target="_blank" rel="noopener">
      <div class="ex-card__img">${x.image ? `<img src="${esc(x.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}${badge}</div>
      <div class="ex-card__body">
        <div class="ex-card__where">${esc(where)}</div>
        <h3>${esc(x.title)}</h3>
        <div class="ex-card__date">${fmtRange(x)}</div>
        ${x.place ? `<div class="ex-card__place">${esc(x.place)}</div>` : ""}
      </div></a>`;
  }

  function renderExhibitions() {
    const today = todayNum();
    const q = exState.q.trim().toLowerCase();
    const list = EX.items.filter((x) =>
      (exState.city === "all" || x.city === exState.city) &&
      (exState.museum === "all" || x.museum === exState.museum) &&
      (!q || `${x.title} ${x.artists} ${x.place} ${x.branch} ${(MUSEUM[x.museum] || {}).name}`.toLowerCase().includes(q)));
    const upcoming = list.filter((x) => dayNum(x.start) > today).sort((a, b) => a.start.localeCompare(b.start));
    const open = list.filter((x) => dayNum(x.start) <= today);
    const ending = open.filter((x) => dayNum(x.end) - today <= ENDING_DAYS).sort((a, b) => a.end.localeCompare(b.end));
    const running = open.filter((x) => dayNum(x.end) - today > ENDING_DAYS).sort((a, b) => b.start.localeCompare(a.start));
    const section = (title, note, xs) => xs.length ? `<section class="ex-sec">
      <div class="sec-head sec-head--sm"><h2>${title} <small>${xs.length}</small></h2><p>${note}</p></div>
      <div class="ex-grid">${xs.map((x) => exCard(x, today)).join("")}</div></section>` : "";
    $("#ex-sections").innerHTML =
      section("곧 끝나는 전시", `${ENDING_DAYS}일 안에 막을 내립니다`, ending) +
      section("진행 중", "최근 개막한 순서", running) +
      section("개막 예정", "개막일이 가까운 순서", upcoming);
    $("#ex-empty").hidden = list.length > 0;
  }

  function initExhibitions() {
    const ok = EX.museums.filter((m) => m.status && m.status.ok).length;
    $("#ex-desc").textContent = `국내 미술관·박물관 ${EX.museums.length}곳의 현재·예정 전시입니다. (수집 성공 ${ok}/${EX.museums.length}곳)`;
    const cities = [...new Set(EX.items.map((x) => x.city))];
    const order = ["서울", "경기", "인천", "충북", "대전", "대구", "부산", "광주"];
    cities.sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
    $("#excity").innerHTML = `<button data-v="all" class="on">전체</button>` + cities.map((c) => `<button data-v="${esc(c)}">${esc(c)}</button>`).join("");
    $("#excity").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      e.currentTarget.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      exState.city = b.dataset.v; renderExhibitions();
    });
    const sel = $("#exmuseum");
    EX.museums.forEach((m) => sel.insertAdjacentHTML("beforeend", `<option value="${esc(m.id)}">${esc(m.name)}</option>`));
    sel.addEventListener("change", () => { exState.museum = sel.value; renderExhibitions(); });
    let t;
    $("#exq").addEventListener("input", (e) => {
      clearTimeout(t);
      t = setTimeout(() => { exState.q = e.target.value; renderExhibitions(); }, 150);
    });
    renderExhibitions();
  }

  function route() {
    const v = { "#all": "all", "#sources": "sources", "#exhibitions": "exhibitions" }[location.hash] || "issues";
    for (const name of ["issues", "exhibitions", "all", "sources"]) $("#view-" + name).hidden = v !== name;
    document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.view === v));
    window.scrollTo(0, 0);
  }

  // 이벤트
  const sel = $("#source");
  DATA.sources.slice().sort((a, b) => a.region.localeCompare(b.region) || a.name.localeCompare(b.name))
    .forEach((s) => sel.insertAdjacentHTML("beforeend", `<option value="${esc(s.id)}">${esc(s.region)} · ${esc(s.name)}</option>`));
  sel.addEventListener("change", () => { state.source = sel.value; state.shown = PAGE; render(); });

  document.querySelectorAll("#lang button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.lang));
  $("#lang").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    e.currentTarget.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    state.lang = b.dataset.v; store.set("lang", state.lang); render(); renderIssues();
  });

  for (const key of ["region", "range"]) {
    $("#" + key).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      e.currentTarget.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      state[key] = key === "range" ? Number(b.dataset.v) : b.dataset.v;
      state.shown = PAGE; render();
    });
  }
  $("#cats").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.cat = b.dataset.v; state.shown = PAGE; render();
  });
  let t;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.q = e.target.value; state.shown = PAGE; render(); }, 150);
  });
  $("#list").addEventListener("click", (e) => {
    if (e.target.id === "more") { state.shown += PAGE; render(); }
  });
  window.addEventListener("hashchange", route);

  renderMeta();
  renderSources();
  renderIssues();
  initExhibitions();
  render();
  route();
})();

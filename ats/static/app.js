const FACTOR_INFO = {
  must_skill: ["Must-have skills", "Share of the JD's required skills evidenced in the resume (listed-only counts 60%)"],
  nice_skill: ["Nice-to-have skills", "Share of preferred skills evidenced"],
  keyword: ["JD keywords", "Coverage of important JD terms"],
  tfidf: ["Text similarity", "TF-IDF similarity to the JD, ranked within this applicant pool"],
  semantic: ["Semantic match", "Embedding similarity to the JD, ranked within this pool"],
  experience: ["Experience", "Internships and work-experience bullets"],
  projects: ["Projects", "Depth of project work"],
  impact: ["Quantified impact", "Bullets with numbers and strong action verbs"],
  leadership: ["Leadership & initiative", "Positions of responsibility, founding, shipping"],
  cgpa: ["CGPA", "Academic score, scaled 6.0 to 9.5"],
  program_fit: ["Program fit", "Degree type vs the JD's preferred programs"],
  branch_fit: ["Branch fit", "Department vs the JD's preferred branches"],
  ats_format: ["ATS format", "Parseable text, standard sections, sensible length"],
  llm: ["AI rubric review", "Gemini scores the resume against the JD competencies, with evidence"],
};
const PRESETS = {
  "Balanced (default)": null,
  "Skills first": {must_skill: 40, nice_skill: 15, keyword: 12, llm: 15, semantic: 8, cgpa: 3, ats_format: 2},
  "AI review first": {llm: 60, must_skill: 12, semantic: 10, cgpa: 5, ats_format: 2},
  "Academics heavy": {cgpa: 40, branch_fit: 15, program_fit: 10, llm: 20, must_skill: 12},
  "Resume quality": {impact: 25, ats_format: 20, experience: 15, projects: 15, leadership: 10, llm: 15},
};
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const hue = i => `hsl(${(i * 47) % 360} 55% 52%)`;
const api = async (url, opt) => {
  const r = await fetch(url, opt);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r;
};
const jpost = (url, body, method = "POST") => api(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});

const S = {slug: null, meta: null, weights: {}, enabled: {}, rows: [], timer: null, job: null};
const store = {
  get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

// ---------- tabs / company ----------
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  showTab(b.dataset.tab);
});

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + name));
  if (name === "jd") renderJD();
}

async function init(pick) {
  const cs = await (await api("/api/companies")).json();
  $("#company").innerHTML = cs.map(c => `<option value="${esc(c.slug)}">${esc(c.title && c.title !== c.slug ? c.title : c.slug)} — ${esc(c.role)} (${c.candidates})</option>`).join("");
  $("#company").onchange = () => loadCompany($("#company").value);
  const want = pick || store.get("slug");
  await loadCompany(cs.some(c => c.slug === want) ? want : cs[0].slug);
}

$("#newForm").onsubmit = async e => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("title", $("#nwTitle").value);
  fd.append("jd_text", $("#nwJdText").value);
  if ($("#nwJdFile").files[0]) fd.append("jd_file", $("#nwJdFile").files[0]);
  for (const f of $("#nwRes").files) fd.append("resumes", f);
  if ($("#nwSheet").files[0]) fd.append("sheet", $("#nwSheet").files[0]);
  $("#nwBtn").disabled = true; $("#nwMsg").textContent = "Uploading and reading resumes… (can take a minute)";
  try {
    const r = await fetch("/api/new-job", {method: "POST", body: fd});
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const j = await r.json();
    $("#nwMsg").textContent = "";
    $("#newForm").reset();
    await init(j.slug); showTab("rank");
    if (S.meta && $("#rubricRun")) $("#rubricRun").click();   // AI review of the top candidates
  } catch (err) { $("#nwMsg").textContent = "Failed: " + err.message; }
  $("#nwBtn").disabled = false;
};

async function loadCompany(slug) {
  S.slug = slug; $("#company").value = slug; store.set("slug", slug);
  S.meta = await (await api(`/api/companies/${slug}`)).json();
  const saved = store.get("cfg:" + slug);
  S.weights = {...S.meta.default_weights, ...(saved?.weights || {})};
  S.enabled = Object.fromEntries(S.meta.factors.map(f => [f, saved?.enabled?.[f] ?? true]));
  $("#fProg").innerHTML = S.meta.programs.map(p => `<option>${esc(p)}</option>`).join("");
  $("#fDept").innerHTML = S.meta.departments.map(p => `<option>${esc(p)}</option>`).join("");
  renderFactors(); renderPresets(); refreshRank();
  const m = S.meta.embed_job;
  $("#notice").hidden = S.meta.semantic || !m;
  if (!S.meta.semantic) pollEmbed();
}

async function pollEmbed() {
  const n = $("#notice"); n.hidden = false;
  const m = (await (await api(`/api/companies/${S.slug}`)).json());
  if (m.semantic) { n.hidden = true; S.meta = m; refreshRank(); return; }
  const j = m.embed_job;
  n.textContent = j && j.state === "failed" ? "Semantic match unavailable: " + j.error
    : `Computing semantic embeddings in the background${j && j.total ? ` (${j.done}/${j.total})` : ""}… ranking works meanwhile, without that factor.`;
  if (!j || j.state !== "failed") setTimeout(pollEmbed, 5000);
}

// ---------- factor panel ----------
function renderFactors() {
  $("#factors").innerHTML = S.meta.factors.map((f, i) => `
    <div class="frow ${S.enabled[f] ? "" : "off"}" data-f="${f}" title="${esc(FACTOR_INFO[f][1])}">
      <input type="checkbox" ${S.enabled[f] ? "checked" : ""}>
      <span><i class="dot" style="background:${hue(i)}"></i>${esc(FACTOR_INFO[f][0])}</span>
      <input type="range" min="0" max="60" value="${S.weights[f]}"><output>${S.weights[f]}</output></div>`).join("");
  document.querySelectorAll(".frow").forEach(r => {
    const f = r.dataset.f, [cb, rg] = r.querySelectorAll("input");
    cb.onchange = () => { S.enabled[f] = cb.checked; r.classList.toggle("off", !cb.checked); changed(); };
    rg.oninput = () => { S.weights[f] = +rg.value; r.querySelector("output").textContent = rg.value; changed(); };
  });
}
function changed() { store.set("cfg:" + S.slug, {weights: S.weights, enabled: S.enabled}); debounceRank(); }
$("#allOn").onclick = () => { S.meta.factors.forEach(f => S.enabled[f] = true); renderFactors(); changed(); };
$("#allOff").onclick = () => { S.meta.factors.forEach(f => S.enabled[f] = false); renderFactors(); changed(); };

function renderPresets() {
  const custom = S.meta.presets || {};
  $("#presetSel").innerHTML = `<option value="">Presets…</option>` +
    Object.keys(PRESETS).map(n => `<option value="b:${esc(n)}">${esc(n)}</option>`).join("") +
    Object.keys(custom).map(n => `<option value="c:${esc(n)}">★ ${esc(n)}</option>`).join("");
}
$("#presetSel").onchange = e => {
  const v = e.target.value; if (!v) return;
  const [k, n] = [v[0], v.slice(2)];
  if (k === "b") {
    const p = PRESETS[n];
    S.weights = {...S.meta.default_weights};
    S.enabled = Object.fromEntries(S.meta.factors.map(f => [f, true]));
    if (p) S.meta.factors.forEach(f => { S.enabled[f] = f in p; if (f in p) S.weights[f] = p[f]; });
  } else {
    const c = S.meta.presets[n];
    S.weights = {...S.meta.default_weights, ...c.weights}; S.enabled = {...c.enabled};
    applyFilters(c.filters || {});
  }
  renderFactors(); changed();
};
$("#presetSave").onclick = async () => {
  const name = prompt("Preset name?"); if (!name) return;
  S.meta.presets = await (await jpost(`/api/companies/${S.slug}/presets/${encodeURIComponent(name)}`,
    {weights: S.weights, enabled: S.enabled, filters: collectFilters()}, "PUT")).json();
  renderPresets(); $("#presetSel").value = "c:" + name;
};
$("#presetDel").onclick = async () => {
  const v = $("#presetSel").value; if (!v.startsWith("c:")) return;
  S.meta.presets = await (await api(`/api/companies/${S.slug}/presets/${encodeURIComponent(v.slice(2))}`, {method: "DELETE"})).json();
  renderPresets();
};

// ---------- filters ----------
function collectFilters() {
  const sel = id => [...$(id).selectedOptions].map(o => o.value);
  const c = $("#fCgpa").value;
  return {min_cgpa: c === "" ? null : +c, programs: sel("#fProg"), departments: sel("#fDept"),
    exclude_placed: $("#fPlaced").checked, min_words: $("#fReadable").checked ? 50 : 0, search: $("#search").value.trim()};
}
function applyFilters(f) {
  $("#fCgpa").value = f.min_cgpa ?? "";
  [...$("#fProg").options].forEach(o => o.selected = (f.programs || []).includes(o.value));
  [...$("#fDept").options].forEach(o => o.selected = (f.departments || []).includes(o.value));
  $("#fPlaced").checked = !!f.exclude_placed; $("#fReadable").checked = !!f.min_words;
}
["#fCgpa", "#fProg", "#fDept", "#fPlaced", "#fReadable", "#search"].forEach(id => $(id).addEventListener("input", debounceRank));
$("#fClear").onclick = () => { applyFilters({}); $("#search").value = ""; refreshRank(); };

// ---------- ranking table ----------
function debounceRank() { clearTimeout(S.timer); S.timer = setTimeout(refreshRank, 150); }
const body = () => ({weights: S.weights, enabled: S.enabled, filters: collectFilters()});

async function refreshRank() {
  const d = await (await jpost(`/api/companies/${S.slug}/rank`, {...body(), limit: 600})).json();
  S.rows = d.rows;
  if (S.openRoll && !$("#drawer").hidden) openDrawer(S.openRoll);   // keep an open candidate panel in sync
  $("#count").textContent = `${d.total} of ${S.meta ? S.meta.profile && "" : ""}${d.total === S.rows.length ? "" : ` (showing ${S.rows.length})`} applicants`.replace(" of  ", " ");
  const scored = d.rows.filter(r => r.summary).length;
  $("#rubricInfo").textContent = scored ? `${scored} of the ranked candidates have an AI review.` : "No AI reviews yet. Run it for the shortlist; it uses Gemini and takes a few minutes.";
  const fs = S.meta.factors;
  $("#tbl tbody").innerHTML = d.rows.map(r => {
    const segs = fs.map((f, i) => r.points[f] > 0 ? `<i title="${esc(FACTOR_INFO[f][0])}: ${r.points[f]}" style="width:${r.points[f]}%;background:${hue(i)}"></i>` : "").join("");
    return `<tr data-roll="${esc(r.roll)}"><td class="num">${r.rank}</td>
      <td class="name"><b>${esc(r.name)}</b><span>${esc(r.roll)}${r.placed ? " · placed" : ""}</span></td>
      <td><div>${esc(r.program)}</div><div class="sub">${esc(r.dept)}${r.spec ? " · " + esc(r.spec) : ""}</div></td>
      <td class="num">${r.cgpa ?? "–"}</td>
      <td><div class="score"><b>${r.score.toFixed(1)}</b><div class="meter"><i style="width:${r.score}%"></i></div></div></td>
      <td><div class="stack">${segs}</div></td><td class="verdict">${esc(r.summary)}</td></tr>`;
  }).join("");
  document.querySelectorAll("#tbl tbody tr").forEach(tr => tr.onclick = () => openDrawer(tr.dataset.roll));
}

// ---------- drawer ----------
async function openDrawer(roll) {
  S.openRoll = roll;
  const d = await (await api(`/api/companies/${S.slug}/candidate/${roll}?${new URLSearchParams({weights: JSON.stringify(S.weights), enabled: JSON.stringify(S.enabled), filters: JSON.stringify(collectFilters())})}`)).json();
  const rub = d.detail.rubric, fs = S.meta.factors;
  const tags = (arr, cls) => arr.map(x => `<span class="tag ${cls}">${esc(x)}</span>`).join("") || `<span class="muted">none</span>`;
  $("#drawerBody").innerHTML = `
    <button class="close" onclick="document.getElementById('drawer').hidden=true">✕</button>
    <h2 style="margin:0">${esc(d.name)}</h2>
    <div class="muted">${esc(d.roll)} · ${esc(d.program)} · ${esc(d.dept)} · CGPA ${d.cgpa ?? "–"}</div>
    <div class="kpis" style="margin:14px 0"><div><span>Score</span><div class="big">${d.score.toFixed(1)}</div></div>
      <div><span>Rank</span><b>#${d.rank}</b> / ${d.total}</div><div><span>Percentile</span><b>top ${(100 - d.percentile + 0.1).toFixed(0)}%</b></div></div>
    <a href="/api/companies/${S.slug}/resume/${esc(d.roll)}" target="_blank">Open resume ↗</a>
    ${rub ? `<h3>AI review <span class="muted">(${rub.fit_score}/100)</span></h3><p>${esc(rub.summary)}</p>
      ${(rub.competency_scores || []).map(c => `<div class="comp"><b>${esc(c.score)}/10</b> ${esc(c.name)}<div class="sub">${esc(c.evidence)}</div></div>`).join("")}
      <div><b>Strengths</b>${tags(rub.strengths || [], "ok")}</div><div><b>Gaps</b>${tags(rub.gaps || [], "warn")}</div>
      ${(rub.red_flags || []).length ? `<div><b>Red flags</b>${tags(rub.red_flags, "miss")}</div>` : ""}` :
      `<p class="muted">No AI review for this candidate yet. Use "AI rubric review" in the sidebar.</p>`}
    <h3>Skills</h3>
    <div><b>Must-have found</b> ${tags(d.detail.matched_must, "ok")}</div><div><b>Must-have missing</b> ${tags(d.detail.missing_must, "miss")}</div>
    <div><b>Nice-to-have found</b> ${tags(d.detail.matched_nice, "ok")}</div>
    <h3>Score breakdown</h3><div class="bars">${fs.map((f, i) => {
      const p = d.parts[f], v = d.features[f];
      return `<div class="brow ${p.weight ? "" : "dim"}"><span><i class="dot" style="background:${hue(i)};display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px"></i>${esc(FACTOR_INFO[f][0])}</span>
        <div class="meter"><i style="width:${v == null ? 0 : v * 100}%"></i></div>
        <span class="num" title="${v == null ? "does not apply" : "raw " + (v * 100).toFixed(0) + "/100"}">${!p.weight ? (v == null ? "n/a" : "off") : "+" + p.points.toFixed(1)}</span></div>`; }).join("")}</div>
    <div class="sub">Right column = points each factor adds to the score under your current weights (off / n/a = not counted). Bar = raw 0–100 value.</div>
    <h3>Resume format</h3>${d.detail.format_flags.length ? tags(d.detail.format_flags, "warn") : `<span class="tag ok">no format issues</span>`}
    <div class="sub">${d.words} words · ${d.pages} page(s)${d.ocr ? " · text recovered via OCR" : ""}</div>`;
  $("#drawer").hidden = false;
}
document.addEventListener("keydown", e => { if (e.key === "Escape") $("#drawer").hidden = true; });

// ---------- rubric job / export ----------
$("#rubricRun").onclick = async () => {
  const btn = $("#rubricRun"); btn.disabled = true; $("#rubricBar").hidden = false;
  try {
    const top = $("#rubricScope").value;
    const {job} = await (await jpost(`/api/companies/${S.slug}/rubric`, {...body(), top: top ? +top : null})).json();
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const j = await (await api(`/api/jobs/${job}`)).json();
      $("#rubricBar i").style.width = (j.total ? 100 * j.done / j.total : 5) + "%";
      $("#rubricInfo").textContent = `Reviewing ${j.done}/${j.total}… (free-tier quota: ~40 resumes/min)`;
      if (j.state !== "running") { if (j.state === "failed") alert(j.error); break; }
    }
  } catch (e) { alert(e.message); }
  btn.disabled = false; $("#rubricBar").hidden = true; refreshRank();
};
$("#addResBtn").onclick = () => $("#addResFile").click();
$("#addResFile").onchange = async e => {
  const files = [...e.target.files]; e.target.value = "";
  if (!files.length) return;
  const fd = new FormData(); files.forEach(f => fd.append("resumes", f));
  const btn = $("#addResBtn"); btn.disabled = true; btn.textContent = "Adding…";
  try {
    const r = await fetch(`/api/companies/${S.slug}/resumes`, {method: "POST", body: fd});
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const j = await r.json();
    await init(S.slug);
    $("#notice").hidden = false;
    $("#notice").textContent = `Added ${j.added.length} resume(s)${j.skipped.length ? `, skipped ${j.skipped.length} unsupported` : ""}. Run the AI review to score them.`;
  } catch (err) { alert("Could not add resumes: " + err.message); }
  btn.disabled = false; btn.textContent = "＋ Add resumes";
};

$("#exportBtn").onclick = async () => {
  const blob = await (await jpost(`/api/companies/${S.slug}/export`, body())).blob();
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${S.slug}_ranking.xlsx`; a.click();
};

// ---------- candidate check ----------
function renderCheck(d, target) {
  const f = S.meta.factors, det = d.detail, sg = d.suggestions;
  const tags = (arr, cls) => arr.map(x => `<span class="tag ${cls}">${esc(x)}</span>`).join("") || `<span class="muted">none</span>`;
  target.innerHTML = `<div class="card"><div class="kpis"><div><span>Fit score</span><div class="big">${d.score}</div></div>
    <div><span>Estimated rank</span><b>#${d.rank_estimate}</b> of ${d.pool}</div><div><span>Better than</span><b>${d.percentile.toFixed(0)}%</b> of applicants</div></div>
    ${det.rubric ? `<p>${esc(det.rubric.summary)}</p>` : ""}
    <h3>Where you stand</h3><div class="bars">${f.map((k, i) => { const v = d.features[k];
      return `<div class="brow ${v == null ? "dim" : ""}"><span>${esc(FACTOR_INFO[k][0])}</span><div class="meter"><i style="width:${v == null ? 0 : v * 100}%"></i></div><span class="num">${v == null ? "n/a" : (v * 100).toFixed(0)}</span></div>`; }).join("")}</div>
    <h3>Skills</h3><div><b>Found</b> ${tags(det.matched_must.concat(det.matched_nice), "ok")}</div><div><b>Missing</b> ${tags(det.missing_must.concat(det.missing_nice), "miss")}</div>
    <div><b>JD keywords you don't use</b> ${tags((det.keywords_missing || []).slice(0, 15), "warn")}</div>
    <h3>ATS format</h3>${det.format_flags.length ? tags(det.format_flags, "warn") : `<span class="tag ok">no format issues</span>`}
    ${det.rubric ? `<h3>Recruiter view</h3><div><b>Strengths</b>${tags(det.rubric.strengths || [], "ok")}</div><div><b>Gaps</b>${tags(det.rubric.gaps || [], "warn")}</div>` : ""}
    ${sg ? `<h3>What to change</h3><ol>${(sg.priority_actions || []).map(a => `<li>${esc(a)}</li>`).join("")}</ol>
      ${(sg.bullet_rewrites || []).map(b => `<div class="comp"><div class="sub">Before</div>${esc(b.original)}<div class="sub" style="margin-top:6px">After</div><b>${esc(b.improved)}</b><div class="sub">${esc(b.why)}</div></div>`).join("")}
      ${(sg.missing_keywords || []).length ? `<div><b>Keywords to add (if true)</b> ${tags(sg.missing_keywords, "")}</div>` : ""}
      ${(sg.ats_tips || []).length ? `<ul class="tight">${sg.ats_tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}` : ""}</div>`;
}
$("#checkForm").onsubmit = async e => {
  e.preventDefault();
  const fd = new FormData(); fd.append("file", $("#chkFile").files[0]); fd.append("slug", S.slug);
  if ($("#chkCgpa").value) fd.append("cgpa", $("#chkCgpa").value);
  fd.append("program", $("#chkProg").value); fd.append("dept", $("#chkDept").value);
  $("#chkBtn").disabled = true; $("#chkMsg").textContent = "Analysing (about 10-20 s)…"; $("#chkOut").innerHTML = "";
  try { renderCheck(await (await api("/api/check", {method: "POST", body: fd})).json(), $("#chkOut")); $("#chkMsg").textContent = ""; }
  catch (err) { $("#chkMsg").textContent = "Error: " + err.message; }
  $("#chkBtn").disabled = false;
};
$("#lookupForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const d = await (await api(`/api/companies/${S.slug}/candidate/${$("#lookupRoll").value.trim()}`)).json();
    $("#lookupOut").innerHTML = `<div class="card"><b>${esc(d.name)}</b> is <b>#${d.rank}</b> of ${d.total} for ${esc(S.slug)} (score ${d.score.toFixed(1)}).
      <button class="link" id="lkOpen">Open full breakdown</button></div>`;
    $("#lkOpen").onclick = () => { document.querySelector('[data-tab=rank]').click(); openDrawer(d.roll); };
  } catch (err) { $("#lookupOut").innerHTML = `<div class="card">${esc(err.message)} (not an applicant to ${esc(S.slug)})</div>`; }
};

// ---------- JD profile ----------
async function renderJD() {
  const p = S.meta.profile, csv = a => (a || []).join(", ");
  $("#jdBody").innerHTML = `<h2>${esc(p.role_title || S.slug)}</h2><p class="muted">${esc(p.summary)}</p>
    <p class="hint">This profile drives every JD-based factor. Edits re-run the skill features instantly. Changing competencies invalidates AI reviews (they re-run on the next click).</p>
    <div class="card jdgrid">
      <label class="f">Must-have skills <textarea id="jdMust" rows="2">${esc(csv(p.must_have_skills))}</textarea></label>
      <label class="f">Nice-to-have skills <textarea id="jdNice" rows="2">${esc(csv(p.nice_to_have_skills))}</textarea></label>
      <label class="f">Keywords <textarea id="jdKw" rows="3">${esc(csv(p.keywords))}</textarea></label>
      <label class="f">Preferred programs <input id="jdProg" value="${esc(csv(p.preferred_programs))}"></label>
      <label class="f">Preferred departments <input id="jdDept" value="${esc(csv(p.preferred_departments))}"></label>
      <div><b>Competencies</b> <span class="hint">(name · what evidence shows it · weight 1-5)</span>
        ${(p.competencies || []).map(c => `<div class="comprow"><input value="${esc(c.name)}"><input value="${esc(c.description)}"><input type="number" min="1" max="5" value="${c.weight}"></div>`).join("")}</div>
      <div class="row"><button class="primary" id="jdSave">Save changes</button>
        <button id="jdRegen">Re-extract from JD file (Gemini)</button><span id="jdMsg" class="muted"></span></div>
    </div>`;
  const list = id => $(id).value.split(",").map(s => s.trim()).filter(Boolean);
  $("#jdSave").onclick = async () => {
    const comps = [...document.querySelectorAll(".comprow")].map(r => { const [n, d, w] = r.querySelectorAll("input"); return {name: n.value.trim(), description: d.value.trim(), weight: +w.value}; }).filter(c => c.name);
    $("#jdMsg").textContent = "Saving…";
    await jpost(`/api/companies/${S.slug}/profile`, {must_have_skills: list("#jdMust"), nice_to_have_skills: list("#jdNice"), keywords: list("#jdKw"),
      preferred_programs: list("#jdProg"), preferred_departments: list("#jdDept"), competencies: comps}, "PUT");
    await loadCompany(S.slug); $("#jdMsg").textContent = "Saved."; renderJD();
  };
  $("#jdRegen").onclick = async () => {
    if (!confirm("Overwrite this profile with a fresh Gemini extraction from the JD file?")) return;
    $("#jdMsg").textContent = "Extracting…"; await api(`/api/companies/${S.slug}/profile/regenerate`, {method: "POST"});
    await loadCompany(S.slug); renderJD();
  };
}

init().catch(e => { document.body.insertAdjacentHTML("beforeend", `<pre style="padding:20px;color:#c0392b">${esc(e.message)}</pre>`); });

/* Terraces & night noise: front-end logic.
   All data comes from the backend API, which queries the group's linked RDF via SPARQL. */
(() => {
  "use strict";

  const DEFAULT_RANGE = ["2025-01-01", "2025-06-30"]; // as in the application mock-ups
  const NOISE_STOPS = [
    [50, [90, 169, 230]],
    [55, [143, 143, 240]],
    [60, [199, 125, 222]],
    [65, [241, 91, 181]],
    [70, [255, 77, 94]],
  ];
  const NONE_COLOR = "#59607a";
  const SMALL_WORDS = new Set(["de", "del", "la", "las", "los", "el", "y"]);

  const $ = (id) => document.getElementById(id);
  const els = {
    radius: $("radius"), radiusValue: $("radius-value"), start: $("start"), end: $("end"),
    error: $("error"),
    showTerraces: $("show-terraces"), corrValue: $("corr-value"), corrStrength: $("corr-strength"),
    corrN: $("corr-n"), corrExcluded: $("corr-excluded"), detail: $("detail"), detailEmpty: $("detail-empty"),
    detailClose: $("detail-close"), dName: $("d-name"), dDistrict: $("d-district"),
    dRadius: $("d-radius"), dTerraces: $("d-terraces"), dRange: $("d-range"), dNoise: $("d-noise"),
    dNights: $("d-nights"), terraceBody: document.querySelector("#terrace-table tbody"),
    rankingBody: document.querySelector("#ranking-table tbody"), rankingNote: $("ranking-note"),
    loading: $("loading"),
  };

  const state = {
    meta: null, stations: [], byCode: new Map(), analysis: null, detail: null,
    radius: 250, start: DEFAULT_RANGE[0], end: DEFAULT_RANGE[1], selected: null,
    rankingSort: { key: "terraces", dir: -1 }, terraceSort: { key: "distance", dir: 1 },
  };

  let map, terraceLayer, highlightLayer, chart;
  const stationLayers = new Map(); // code -> {circle, marker}
  let analysisCtrl = null, detailCtrl = null, debounceTimer = null;

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // "VILLA DE VALLECAS" -> "Villa de Vallecas", "FUENCARRAL-EL PARDO" -> "Fuencarral-El Pardo"
  const titleCase = (s) => (s ? s.toLowerCase().replace(/\p{L}+/gu, (w, i, str) =>
    (i > 0 && str[i - 1] === " " && SMALL_WORDS.has(w)) ? w : w[0].toUpperCase() + w.slice(1)) : "");

  const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB",
    { day: "numeric", month: "short", year: "numeric" });
  const fmtNum = (n) => n.toLocaleString("en-GB");

  function noiseColor(db) {
    if (db == null) return NONE_COLOR;
    if (db <= NOISE_STOPS[0][0]) return rgb(NOISE_STOPS[0][1]);
    for (let i = 1; i < NOISE_STOPS.length; i++) {
      const [v1, c1] = NOISE_STOPS[i];
      const [v0, c0] = NOISE_STOPS[i - 1];
      if (db <= v1) {
        const t = (db - v0) / (v1 - v0);
        return rgb(c0.map((c, k) => Math.round(c + (c1[k] - c) * t)));
      }
    }
    return rgb(NOISE_STOPS[NOISE_STOPS.length - 1][1]);
  }
  function rgb([r, g, b]) { return `rgb(${r}, ${g}, ${b})`; }

  // Place name linked to its Wikidata entity (owl:sameAs in the RDF), without showing the Q-id.
  function placeLink(label, uri) {
    if (!label) return "—";
    if (!uri) return esc(label);
    return `<a class="wd" href="${esc(uri)}" target="_blank" rel="noopener" title="${esc(label)} on Wikidata">${esc(label)}</a>`;
  }

  async function getJSON(url, signal) {
    const res = await fetch(url, { signal });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* keep statusText */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  function showError(msg) {
    els.error.hidden = !msg;
    els.error.textContent = msg || "";
  }

  function params(extra = {}) {
    return new URLSearchParams({ radius: state.radius, start: state.start, end: state.end, ...extra });
  }

  // URL hash keeps the current view shareable: #radius=250&start=...&end=...&station=RF-03
  function writeHash() {
    const p = params();
    if (state.selected) p.set("station", state.selected);
    history.replaceState(null, "", `#${p}`);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const r = Number(p.get("radius"));
    if (r >= 50 && r <= 1000) state.radius = Math.round(r / 25) * 25;
    const iso = /^\d{4}-\d{2}-\d{2}$/;
    if (iso.test(p.get("start") || "") && iso.test(p.get("end") || "")) {
      state.start = p.get("start"); state.end = p.get("end");
    }
    if (p.get("station")) state.selected = p.get("station");
  }

  // ---------------------------------------------------------------- controls
  function syncRadiusUI() {
    els.radius.value = state.radius;
    els.radiusValue.textContent = `${state.radius} m`;
    const pct = ((state.radius - els.radius.min) / (els.radius.max - els.radius.min)) * 100;
    els.radius.style.setProperty("--fill", `${pct}%`);
    document.querySelectorAll("[data-radius]").forEach((b) =>
      b.setAttribute("aria-pressed", String(Number(b.dataset.radius) === state.radius)));
  }

  function syncDateUI() {
    els.start.value = state.start; els.end.value = state.end;
    els.start.max = state.end; els.end.min = state.start;
  }

  function bindControls() {
    els.radius.addEventListener("input", () => {
      state.radius = Number(els.radius.value);
      syncRadiusUI(); refreshSoon();
    });
    document.querySelectorAll("[data-radius]").forEach((b) => b.addEventListener("click", () => {
      state.radius = Number(b.dataset.radius); syncRadiusUI(); refresh();
    }));
    const onDate = () => {
      const s = els.start.value, e = els.end.value;
      if (!s || !e) return;
      if (s > e) { showError("The start date must be on or before the end date."); return; }
      state.start = s; state.end = e; syncDateUI(); refresh();
    };
    els.start.addEventListener("change", onDate);
    els.end.addEventListener("change", onDate);
    els.showTerraces.addEventListener("change", () => {
      if (els.showTerraces.checked) terraceLayer.addTo(map); else terraceLayer.remove();
    });
    els.detailClose.addEventListener("click", () => select(null));
    bindSortable("#ranking-table", state.rankingSort, renderRanking);
    bindSortable("#terrace-table", state.terraceSort, renderTerraceTable);
  }

  function bindSortable(selector, sort, render) {
    const ths = document.querySelectorAll(`${selector} th[data-key]`);
    ths.forEach((th) => {
      th.tabIndex = 0;
      const activate = () => {
        const key = th.dataset.key;
        sort.dir = sort.key === key ? -sort.dir : (th.classList.contains("num") ? -1 : 1);
        sort.key = key;
        ths.forEach((o) => o.removeAttribute("aria-sort"));
        th.setAttribute("aria-sort", sort.dir === 1 ? "ascending" : "descending");
        render();
      };
      th.addEventListener("click", activate);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
    });
  }

  function sortRows(rows, { key, dir }) {
    return rows.slice().sort((a, b) => {
      const va = a[key], vb = b[key];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;            // missing values always last
      if (vb == null) return -1;
      if (typeof va === "string") return va.localeCompare(vb, "es") * dir;
      return (va - vb) * dir;
    });
  }

  // ---------------------------------------------------------------- map
  function initMap(terraces) {
    map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 10, maxZoom: 18 })
      .setView([40.428, -3.69], 12);
    // Standard OpenStreetMap tiles (no API key), darkened with a CSS filter (.basemap in styles.css).
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · Data: Ayuntamiento de Madrid (CC BY 4.0)',
      className: "basemap", maxZoom: 19,
    }).addTo(map);

    const renderer = L.canvas({ padding: 0.4 });
    terraceLayer = L.layerGroup(terraces.map(([, lat, lon]) => L.circleMarker([lat, lon], {
      renderer, radius: 1.7, stroke: false, fillColor: "#ffb347", fillOpacity: 0.55, interactive: false,
    }))).addTo(map);
    highlightLayer = L.layerGroup().addTo(map);

    const bounds = [];
    for (const s of state.stations) {
      const ll = [s.lat, s.lon];
      bounds.push(ll);
      const circle = L.circle(ll, {
        radius: state.radius, color: NONE_COLOR, weight: 1.2, dashArray: "4 4", fillOpacity: 0.06, interactive: false,
      }).addTo(map);
      const marker = L.marker(ll, { icon: pinIcon(s, null), keyboard: true, title: s.name, riseOnHover: true })
        .addTo(map)
        .on("click", () => select(s.code, "map"));
      marker.bindTooltip("", { className: "station-tip", direction: "top", offset: [0, -14] });
      stationLayers.set(s.code, { circle, marker });
    }
    map.fitBounds(bounds, { padding: [30, 30] });
  }

  function pinIcon(station, row) {
    const count = row ? row.terraces : "·";
    const size = row ? Math.min(54, 26 + Math.sqrt(row.terraces) * 2.6) : 26;
    const color = row ? noiseColor(row.meanLaeq) : NONE_COLOR;
    const cls = ["station-pin", row && !row.included ? "excluded" : "", state.selected === station.code ? "selected" : ""].join(" ");
    return L.divIcon({
      className: "",
      html: `<div class="${cls}" style="background:${color}">${count}</div>`,
      iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    });
  }

  function updateMap() {
    const rows = new Map(state.analysis.stations.map((r) => [r.code, r]));
    for (const s of state.stations) {
      const row = rows.get(s.code);
      const { circle, marker } = stationLayers.get(s.code);
      const color = noiseColor(row.meanLaeq);
      circle.setRadius(state.radius);
      circle.setStyle({ color, fillColor: color, fillOpacity: state.selected === s.code ? 0.14 : 0.06,
        weight: state.selected === s.code ? 2 : 1.2 });
      marker.setIcon(pinIcon(s, row));
      marker.setZIndexOffset(state.selected === s.code ? 1000 : row.terraces);
      const noise = row.included ? `${row.meanLaeq.toFixed(1)} dB(A) · ${row.nights} nights` : "No night data in range";
      marker.setTooltipContent(`<b>${esc(s.name)}</b><br>` +
        `${row.terraces} terraces within ${state.radius} m<br>${noise}`);
    }
  }

  function updateHighlight() {
    highlightLayer.clearLayers();
    if (!state.detail) return;
    for (const t of state.detail.terraces) {
      L.circleMarker([t.lat, t.lon], {
        radius: 3.2, color: "#fff3dc", weight: 0.8, fillColor: "#ffb347", fillOpacity: 1, interactive: false,
      }).addTo(highlightLayer);
    }
  }

  // ---------------------------------------------------------------- chart
  function initChart() {
    Chart.defaults.color = "#a5abc0";
    Chart.defaults.font.family = '"IBM Plex Mono", ui-monospace, monospace';
    Chart.defaults.font.size = 11;
    chart = new Chart($("scatter"), {
      type: "scatter",
      data: {
        datasets: [
          { type: "scatter", label: "Stations", data: [], pointRadius: [], pointHoverRadius: [],
            pointBackgroundColor: [], pointBorderColor: [], pointBorderWidth: [] },
          { type: "line", label: "Least-squares fit", data: [], borderColor: "rgba(236,230,214,0.55)",
            borderDash: [6, 5], borderWidth: 1.4, pointRadius: 0, pointHoverRadius: 0, fill: false },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
        interaction: { mode: "nearest", intersect: true },
        scales: {
          x: { type: "linear", beginAtZero: true,
            title: { display: true, text: "Terraces within radius" },
            grid: { color: "rgba(160,178,230,0.09)" }, border: { color: "rgba(160,178,230,0.25)" } },
          y: { title: { display: true, text: "Mean night LAeq, dB(A)" },
            grid: { color: "rgba(160,178,230,0.09)" }, border: { color: "rgba(160,178,230,0.25)" } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: (item) => item.datasetIndex === 0,
            backgroundColor: "#151d36", borderColor: "rgba(160,178,230,0.28)", borderWidth: 1, padding: 10,
            titleFont: { family: '"Hanken Grotesk", sans-serif', size: 13, weight: "600" },
            callbacks: {
              title: (items) => items[0].raw.name,
              label: (item) => [`${item.raw.x} terraces within ${state.radius} m`, `${item.raw.y.toFixed(1)} dB(A) mean night LAeq`],
            },
          },
        },
        onClick: (evt) => {
          const hit = chart.getElementsAtEventForMode(evt, "nearest", { intersect: true }, false)
            .find((e) => e.datasetIndex === 0);
          if (hit) select(chart.data.datasets[0].data[hit.index].code, "chart");
        },
        onHover: (evt, active) => {
          evt.native.target.style.cursor = active.some((a) => a.datasetIndex === 0) ? "pointer" : "default";
        },
      },
    });
  }

  function updateChart() {
    const a = state.analysis;
    const pts = a.stations.filter((r) => r.included).map((r) => ({ x: r.terraces, y: r.meanLaeq, code: r.code, name: r.name }));
    const ds = chart.data.datasets[0];
    ds.data = pts;
    ds.pointBackgroundColor = pts.map((p) => noiseColor(p.y));
    ds.pointRadius = pts.map((p) => (p.code === state.selected ? 10 : 6));
    ds.pointHoverRadius = pts.map((p) => (p.code === state.selected ? 12 : 8));
    ds.pointBorderColor = pts.map((p) => (p.code === state.selected ? "#ece6d6" : "rgba(11,16,32,0.9)"));
    ds.pointBorderWidth = pts.map((p) => (p.code === state.selected ? 3 : 1.5));
    const line = chart.data.datasets[1];
    if (a.fit && pts.length) {
      const xs = pts.map((p) => p.x);
      const x0 = Math.min(...xs), x1 = Math.max(...xs);
      line.data = [{ x: x0, y: a.fit.slope * x0 + a.fit.intercept }, { x: x1, y: a.fit.slope * x1 + a.fit.intercept }];
    } else {
      line.data = [];
    }
    chart.update();
  }

  // ---------------------------------------------------------------- stats & tables
  function updateCorrelation() {
    const { correlation: c, excluded, stations } = state.analysis;
    if (c.r == null) {
      els.corrValue.textContent = "—";
      els.corrStrength.textContent = c.n < 3 ? "Not enough stations with data" : "Undefined (no variation)";
    } else {
      els.corrValue.textContent = (c.r > 0 ? "+" : "") + c.r.toFixed(2);
      els.corrStrength.textContent = c.strength[0].toUpperCase() + c.strength.slice(1);
    }
    els.corrN.textContent = `${c.n} of ${stations.length} stations used · radius ${state.radius} m · ${fmtDate(state.start)} – ${fmtDate(state.end)}`;
    if (excluded.length) {
      const names = excluded.map((e) => e.name).join(", ");
      els.corrExcluded.textContent = `${excluded.length} station${excluded.length > 1 ? "s" : ""} left out (no night data in range): ${names}`;
      els.corrExcluded.classList.add("warn");
    } else {
      els.corrExcluded.textContent = "0 stations left out: every station has night data in this range.";
      els.corrExcluded.classList.remove("warn");
    }
  }

  function renderRanking() {
    if (!state.analysis) return;
    const rows = state.analysis.stations.map((r) => {
      const s = state.byCode.get(r.code);
      return { ...r, district: s.district ? titleCase(s.district.label) : null, station: s };
    });
    els.rankingNote.textContent = `terraces within ${state.radius} m`;
    els.rankingBody.innerHTML = sortRows(rows, state.rankingSort).map((r) => {
      const d = r.station.district;
      return `<tr data-code="${esc(r.code)}" tabindex="0" class="${r.included ? "" : "excluded"} ${r.code === state.selected ? "selected" : ""}">
        <td><div class="station-cell"><span class="swatch" style="background:${noiseColor(r.meanLaeq)}"></span><b>${esc(r.name)}</b></div></td>
        <td>${d ? placeLink(r.district, d.wikidata) : "—"}</td>
        <td class="num">${r.terraces}</td>
        <td class="num">${r.included ? r.meanLaeq.toFixed(1) : "no data"}</td>
        <td class="num">${r.nights}</td>
      </tr>`;
    }).join("");
  }

  function bindRankingRows() {
    const pick = (ev) => {
      if (ev.target.closest("a")) return;
      const tr = ev.target.closest("tr[data-code]");
      if (tr) select(tr.dataset.code, "table");
    };
    els.rankingBody.addEventListener("click", pick);
    els.rankingBody.addEventListener("keydown", (ev) => { if (ev.key === "Enter") pick(ev); });
  }

  function renderTerraceTable() {
    if (!state.detail) return;
    const rows = state.detail.terraces.map((t) => ({
      ...t, district: titleCase(t.district), neighbourhood: titleCase(t.neighbourhood),
    }));
    if (!rows.length) {
      els.terraceBody.innerHTML = `<tr><td colspan="3" class="muted">No registered terrace with a usable location within ${state.radius} m.</td></tr>`;
      return;
    }
    els.terraceBody.innerHTML = sortRows(rows, state.terraceSort).map((t) => `<tr>
      <td class="num">${Math.round(t.distance)} m</td>
      <td>${placeLink(t.neighbourhood, t.neighbourhoodWikidata)}</td>
      <td>${placeLink(t.district, t.districtWikidata)}</td>
    </tr>`).join("");
  }

  function renderDetail() {
    const d = state.detail;
    const has = Boolean(d);
    els.detail.hidden = !has; els.detailEmpty.hidden = has; els.detailClose.hidden = !has;
    if (!has) return;
    els.dName.textContent = d.name;
    els.dDistrict.innerHTML = d.district
      ? `District: ${placeLink(titleCase(d.district.label), d.district.wikidata)}
         <span class="muted">· catalogue location ${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}</span>`
      : "";
    els.dRadius.textContent = `${d.radius} m`;
    els.dTerraces.textContent = fmtNum(d.terraceCount);
    els.dRange.textContent = `${fmtDate(d.start)} – ${fmtDate(d.end)}`;
    els.dNoise.innerHTML = d.meanLaeq == null ? "No data" : `${d.meanLaeq.toFixed(1)}<small>dB(A)</small>`;
    els.dNights.textContent = fmtNum(d.nights);
    renderTerraceTable();
  }

  // ---------------------------------------------------------------- data flow
  function refreshSoon() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refresh, 140);
  }

  async function refresh() {
    clearTimeout(debounceTimer);
    writeHash();
    analysisCtrl?.abort();
    analysisCtrl = new AbortController();
    try {
      const [analysis] = await Promise.all([
        getJSON(`api/analysis?${params()}`, analysisCtrl.signal),
        state.selected ? loadDetail() : Promise.resolve(),
      ]);
      state.analysis = analysis;
      showError("");
      updateMap(); updateChart(); updateCorrelation(); renderRanking(); updateHighlight(); renderDetail();
    } catch (err) {
      if (err.name !== "AbortError") showError(`Could not update: ${err.message}`);
    }
  }

  async function loadDetail() {
    detailCtrl?.abort();
    detailCtrl = new AbortController();
    if (!state.selected) { state.detail = null; return; }
    state.detail = await getJSON(`api/stations/${encodeURIComponent(state.selected)}?${params()}`, detailCtrl.signal);
  }

  async function select(code, source) {
    if (code && !state.byCode.has(code)) code = null;
    state.selected = code;
    writeHash();
    try {
      await loadDetail();
    } catch (err) {
      if (err.name === "AbortError") return;
      showError(`Could not load the station: ${err.message}`);
    }
    if (state.analysis) { updateMap(); updateChart(); renderRanking(); }
    updateHighlight(); renderDetail();
    if (code && source && source !== "map") {
      const { circle } = stationLayers.get(code);
      map.flyToBounds(circle.getBounds(), { maxZoom: 16, padding: [40, 40], duration: 0.8 });
    }
    if (code && source && window.matchMedia("(max-width: 1100px)").matches) {
      $("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function init() {
    readHash();
    syncRadiusUI();
    try {
      const [meta, stations, terraces] = await Promise.all([
        getJSON("api/meta"), getJSON("api/stations"), getJSON("api/terraces"),
      ]);
      state.meta = meta;
      state.stations = stations;
      stations.forEach((s) => state.byCode.set(s.code, s));
      if (state.selected && !state.byCode.has(state.selected)) state.selected = null;
      const clamp = (d) => (d < meta.firstNight ? meta.firstNight : d > meta.lastNight ? meta.lastNight : d);
      state.start = clamp(state.start); state.end = clamp(state.end);
      if (state.start > state.end) [state.start, state.end] = DEFAULT_RANGE;
      for (const el of [els.start, els.end]) { el.min = meta.firstNight; el.max = meta.lastNight; }
      syncDateUI(); bindControls(); bindRankingRows();
      initMap(terraces); initChart();
      await refresh();
    } catch (err) {
      showError(`Could not load the linked data: ${err.message}. Is the backend running?`);
    } finally {
      els.loading.classList.add("done");
      document.body.classList.add("ready");
    }
  }

  init();
})();

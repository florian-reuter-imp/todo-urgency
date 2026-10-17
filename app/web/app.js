"use strict";

const PRIO = {
  highest: "🔺", high: "⏫", medium: "🔼", low: "🔽", lowest: "⏬", none: "",
};
const PRIO_LABEL = {
  none: "– Priorität –", highest: "🔺 höchste", high: "⏫ hoch",
  medium: "🔼 mittel", low: "🔽 niedrig", lowest: "⏬ niedrigste",
};
const PRIO_KEYS = ["highest", "high", "medium", "none", "low", "lowest"];
const QUAD_COLOR = {
  do: "var(--do)", plan: "var(--plan)", delegate: "var(--delegate)", drop: "var(--drop)",
};
const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const WEEKDAY_LABEL = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

const state = {
  data: null,
  view: "list",
  showDone: false,
  showSnoozed: false,
  groupTags: false,
  effortLimit: "",
  selected: null,
  editing: null,
  editRaw: false,
  timeOffset: 0,
  weights: null,
  saved: null,
  defaults: null,
  weightsOpen: false,
  weightsMode: "simple",
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, kids = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "value") node.value = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const kid of kids) if (kid) node.append(kid);
  return node;
};
const round2 = (v) => Math.round(v * 100) / 100;
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

const isReadOnly = () => state.timeOffset > 0;
const weightsDirty = () =>
  state.weights && state.saved && JSON.stringify(state.weights) !== JSON.stringify(state.saved);

// --- Zahl + Einheit --------------------------------------------------
/** Aufwand als Zahl plus Einheit. read() liefert "90m" / "2h" / "" . */
function effortControl(minutes) {
  let value = "", unit = "m";
  if (minutes) {
    if (minutes % 480 === 0) { value = minutes / 480; unit = "d"; }
    else if (minutes % 60 === 0) { value = minutes / 60; unit = "h"; }
    else { value = minutes; unit = "m"; }
  }
  const num = el("input", {
    type: "number", min: "0", step: "1", value, placeholder: "–",
    title: "Geschätzter Aufwand – leer lassen für keine Angabe",
  });
  const sel = el("select", { class: "unit", title: "Einheit" }, [
    el("option", { value: "m", text: "Minuten", selected: unit === "m" && "selected" }),
    el("option", { value: "h", text: "Stunden", selected: unit === "h" && "selected" }),
    el("option", { value: "d", text: "Arbeitstage", selected: unit === "d" && "selected" }),
  ]);
  const node = el("span", { class: "qty" }, [el("span", { text: "⏱" }), num, sel]);
  const sync = () => node.classList.toggle("is-empty", !num.value);
  num.addEventListener("input", sync);
  sync();
  return { node, read: () => (num.value && Number(num.value) > 0 ? `${num.value}${sel.value}` : "") };
}

/** Wiederholung als Dropdowns. read() liefert kanonischen Tasks-Text. */
function recurrenceControl(rule, rawFallback) {
  const kind = rule
    ? (rule.weekday !== null && rule.weekday !== undefined ? "weekday" : rule.unit)
    : "";
  const kindSel = el("select", { title: "Wiederholung" }, [
    ["", "nicht wiederkehrend"], ["day", "alle … Tage"], ["week", "alle … Wochen"],
    ["month", "alle … Monate"], ["year", "alle … Jahre"], ["weekday", "an einem Wochentag"],
  ].map(([v, t]) => el("option", { value: v, text: t, selected: v === kind && "selected" })));

  const num = el("input", {
    type: "number", min: "1", step: "1", value: rule ? rule.interval : 1, title: "Intervall",
  });
  const day = el("select", { title: "Wochentag" }, WEEKDAYS.map((v, i) =>
    el("option", { value: v, text: WEEKDAY_LABEL[i], selected: i === rule?.weekday && "selected" })));
  const whenDone = el("input", { type: "checkbox", checked: rule?.when_done && "checked" });
  const whenDoneLabel = el("label", { class: "toggle" }, [whenDone, el("span", { text: "ab Erledigung" })]);

  const node = el("span", { class: "qty" }, [kindSel, num, day, whenDoneLabel]);
  const sync = () => {
    const k = kindSel.value;
    num.hidden = !k || k === "weekday";
    day.hidden = k !== "weekday";
    whenDoneLabel.hidden = !k;
  };
  kindSel.addEventListener("change", sync);
  sync();

  return {
    node,
    read: () => {
      const k = kindSel.value;
      // Unerkannte Regel aus der Datei nicht wegwerfen, nur weil die
      // Dropdowns sie nicht darstellen koennen.
      if (!k) return rule === null && rawFallback ? rawFallback : "";
      const base = k === "weekday"
        ? `every ${day.value}`
        : `every ${Math.max(Number(num.value) || 1, 1)} ${k}s`;
      return base + (whenDone.checked ? " when done" : "");
    },
  };
}

// --- API -------------------------------------------------------------
async function api(path, options) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    setStatus(body.error || `Fehler ${res.status}`, true);
    if (body.reload) load();
    throw new Error(body.error || res.status);
  }
  setStatus("");
  return body;
}

function setStatus(text, isError = false) {
  const node = $("#status");
  node.textContent = text;
  node.classList.toggle("error", isError);
}

function todayParam() {
  if (!state.timeOffset) return "";
  const d = new Date();
  d.setDate(d.getDate() + state.timeOffset);
  return `?today=${d.toISOString().slice(0, 10)}`;
}

async function load() {
  if (weightsDirty()) {
    render(await api(`/api/tasks/preview${todayParam()}`, {
      method: "POST",
      body: JSON.stringify({ urgency: state.weights }),
    }));
  } else {
    render(await api(`/api/tasks${todayParam()}`));
  }
}

function render(data) {
  if (!data) return;
  state.data = data;
  applyFeatures(data.features || {});
  renderHistory();
  renderBanner();
  if (state.view === "list") renderList();
  else renderMatrix();
}

function applyFeatures(features) {
  for (const node of document.querySelectorAll("[data-feature]")) {
    node.classList.toggle("is-off", features[node.dataset.feature] === false);
  }
  if (features.effort === false) state.effortLimit = "";
  if (features.grouping === false) state.groupTags = false;
}

// --- Rückgängig / Wiederholen ----------------------------------------
let toastTimer = null;

/** Kurzer Hinweis unten links, mit genau einer Folgeaktion. */
function toast(text, actionLabel, action, isError = false) {
  const box = $("#toast");
  box.replaceChildren(el("span", { text }));
  if (actionLabel) {
    box.append(el("button", {
      type: "button", text: actionLabel,
      onclick: () => { hideToast(); action(); },
    }));
  }
  box.classList.toggle("is-error", isError);
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, isError ? 9000 : 7000);
}
const hideToast = () => { $("#toast").hidden = true; };

function renderHistory() {
  const h = (state.data && state.data.history) || { undo: {}, redo: {} };
  for (const [id, info, verb] of [["#undo", h.undo, "Rückgängig"], ["#redo", h.redo, "Wiederholen"]]) {
    const btn = $(id);
    btn.disabled = !info.can || isReadOnly();
    btn.title = info.can
      ? `${verb}: ${info.label} (${id === "#undo" ? "Strg+Z" : "Strg+Y"})`
      : `Nichts ${verb === "Rückgängig" ? "rückgängig zu machen" : "zu wiederholen"}`;
  }
}

/** Nach jedem Schreibvorgang: anbieten, es sofort zurückzunehmen. */
function offerUndo() {
  const label = state.data?.history?.undo?.label;
  if (label) toast(label, "Rückgängig", doUndo);
}

async function doUndo() {
  if (isReadOnly()) return;
  try {
    const data = await api("/api/undo", { method: "POST" });
    render(data);
    toast(`${data.undone} – rückgängig gemacht`, "Wiederholen", doRedo);
  } catch (err) {
    toast(String(err.message || err), null, null, true);
  }
}

async function doRedo() {
  if (isReadOnly()) return;
  try {
    const data = await api("/api/redo", { method: "POST" });
    render(data);
    toast(`${data.redone} – wiederhergestellt`, "Rückgängig", doUndo);
  } catch (err) {
    toast(String(err.message || err), null, null, true);
  }
}

function renderBanner() {
  const box = $("#banner");
  const notes = [];
  if (isReadOnly()) notes.push(`Zeitreise: Stand ${state.data.today} – Bearbeiten ist gesperrt.`);
  if (weightsDirty()) notes.push("Gewichte probeweise verstellt, noch nicht gespeichert.");
  box.textContent = notes.join("  ·  ");
  box.hidden = notes.length === 0;
}

// --- Auswahl ---------------------------------------------------------
function visibleTasks() {
  const all = state.data ? state.data.tasks : [];
  let tasks = state.showDone ? all : all.filter((t) => !t.done);
  if (!state.showSnoozed) tasks = tasks.filter((t) => !t.snoozed);
  let hiddenByEffort = 0;
  if (state.effortLimit) {
    const limit = Number(state.effortLimit);
    const before = tasks.length;
    tasks = tasks.filter((t) => t.effort !== null && t.effort <= limit);
    hiddenByEffort = before - tasks.length;
  }
  return { tasks, hiddenByEffort };
}

function dueInfo(task, today) {
  if (!task.due) return null;
  const days = Math.round((new Date(task.due) - new Date(today)) / 86400000);
  if (days < 0) return { text: `${-days} d überfällig`, cls: "overdue" };
  if (days === 0) return { text: "heute", cls: "today" };
  if (days === 1) return { text: "morgen", cls: "today" };
  return { text: `in ${days} d`, cls: "" };
}

function breakdownText(task) {
  const b = task.breakdown;
  let out = `Dringlichkeit ${task.urgency}  (Fälligkeit ${b.due} + Alter ${b.age})\n`
    + `Wichtigkeit ${task.importance}  (Priorität ${b.priority} + Tags ${b.tags})\n`
    + `= Score ${task.score}`;
  if (task.due) out += `\n\nSteigt ab ${task.horizon_days} Tagen vor der Frist`;
  if (task.effort) out += ` (Aufwand ${task.effort_text})`;
  if (task.start) out += `\nZurückgestellt bis ${task.start}`;
  if (task.recurrence_text) out += `\nWiederholung: ${task.recurrence_text}`;
  return out;
}

async function patch(task, changes) {
  if (isReadOnly()) return;
  const data = await api(`/api/tasks/${task.uid}`, {
    method: "PATCH", body: JSON.stringify(changes),
  });
  if (weightsDirty() || state.timeOffset) await load();
  else render(data);
  offerUndo();
}

const toggleDone = (task) => patch(task, { done: !task.done });

async function remove(task) {
  // Rückfrage bleibt: der Undo-Stapel lebt nur, solange der Server läuft,
  // und der beendet sich nach 20 Minuten ohne offenes Fenster von selbst.
  // Ohne Archiv wäre ein Fehlklick danach endgültig.
  if (isReadOnly() || !confirm(`"${task.description}" löschen?`)) return;
  await api(`/api/tasks/${task.uid}`, { method: "DELETE" });
  await load();
  offerUndo();
}

// --- Listenansicht ---------------------------------------------------
function renderList() {
  const root = $("#view-list");
  root.replaceChildren();
  const { tasks, hiddenByEffort } = visibleTasks();

  if (!tasks.length) {
    root.append(el("p", {
      class: "empty",
      text: state.effortLimit ? "Nichts, was in die Zeit passt." : "Nichts offen. 🎉",
    }));
  } else if (state.groupTags) {
    for (const [tag, group] of groupByTag(tasks)) {
      root.append(el("div", { class: "group-head" }, [
        el("span", { text: tag === null ? "ohne Projekt" : "#" + tag }),
      ]));
      for (const task of group) root.append(rowFor(task));
    }
  } else {
    for (const task of tasks) root.append(rowFor(task));
  }

  if (hiddenByEffort) {
    root.append(el("p", {
      class: "hint",
      text: `${hiddenByEffort} Aufgabe(n) ausgeblendet: über dem Limit oder ohne Aufwandsangabe.`,
    }));
  }
}

function groupByTag(tasks) {
  const groups = new Map();
  for (const task of tasks) {
    const key = task.tags.length ? task.tags[0] : null;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(task);
  }
  return [...groups.entries()].sort((a, b) => {
    if (a[0] === null) return 1;
    if (b[0] === null) return -1;
    return b[1][0].score - a[1][0].score;
  });
}

function rowFor(task) {
  if (state.editing === task.uid) return editFor(task);

  const due = dueInfo(task, state.data.today);
  const classes = ["row", task.done ? "done" : "", task.snoozed ? "snoozed" : ""];
  const row = el("div", { class: classes.filter(Boolean).join(" ") });
  row.style.setProperty("--q", QUAD_COLOR[task.quadrant]);

  const box = el("input", { type: "checkbox", disabled: isReadOnly() });
  box.checked = task.done;
  box.addEventListener("change", () => toggleDone(task));

  const startEdit = () => {
    if (isReadOnly()) return;
    state.editing = task.uid;
    state.editRaw = false;
    renderList();
  };

  // Nulls herausfiltern: append(null) wuerde den Text "null" einfuegen.
  row.append(...[
    box,
    el("span", {
      class: "title", text: task.description,
      title: isReadOnly() ? task.description : "Klicken zum Bearbeiten", onclick: startEdit,
    }),
    PRIO[task.priority] ? el("span", { class: "badge", text: PRIO[task.priority] }) : null,
    task.recurrence_text
      ? el("span", { class: "badge", title: task.recurrence_text, text: "🔁" }) : null,
    task.effort_text ? el("span", { class: "badge", text: "⏱ " + task.effort_text }) : null,
    task.snoozed
      ? el("span", { class: "badge snoozed", text: `🛫 ab ${task.start}` })
      : (due ? el("span", { class: `badge ${due.cls}`, text: due.text }) : null),
    el("span", { class: "score", title: breakdownText(task), text: task.score.toFixed(1) }),
    !isReadOnly() && !task.done
      ? el("button", {
          class: "icon-btn", title: "Eine Woche zurückstellen", text: "🛫",
          onclick: () => patch(task, { snooze_days: 7 }),
        }) : null,
    !isReadOnly() ? el("button", { class: "icon-btn", title: "Bearbeiten", text: "✎", onclick: startEdit }) : null,
    !isReadOnly() ? el("button", { class: "icon-btn danger", title: "Löschen", text: "✕", onclick: () => remove(task) }) : null,
  ].filter(Boolean));
  return row;
}

function editFor(task) {
  const features = state.data.features || {};
  const stop = () => { state.editing = null; state.editRaw = false; renderList(); };

  if (state.editRaw) {
    const raw = el("input", { type: "text", value: task.raw, spellcheck: "false" });
    const form = el("form", {
      class: "edit raw",
      onsubmit: async (e) => { e.preventDefault(); await patch(task, { raw: raw.value }); stop(); },
    }, [
      raw,
      el("button", { type: "submit", text: "Speichern" }),
      el("button", { type: "button", class: "icon-btn", text: "Felder",
        onclick: () => { state.editRaw = false; renderList(); } }),
      el("button", { type: "button", class: "icon-btn", text: "Abbrechen", onclick: stop }),
      el("span", { class: "hint raw-hint",
        text: "Felder: 📅 fällig · ➕ erstellt · 🛫 nicht vor · ✅ erledigt am · "
            + "⏱ Aufwand (20m, 2h, 1d) · 🔁 every 2 weeks · 🔺⏫🔼🔽⏬ Priorität · #tag" }),
    ]);
    setTimeout(() => raw.focus(), 0);
    return form;
  }

  const text = el("input", { type: "text", value: task.description, required: "required" });
  const dueField = el("input", { type: "date", value: task.due || "", title: "Fällig am" });
  const prio = el("select", {}, PRIO_KEYS.map((key) =>
    el("option", { value: key, text: PRIO_LABEL[key], selected: key === task.priority && "selected" })));
  const start = el("input", { type: "date", value: task.start || "", title: "🛫 nicht vor" });
  const effort = effortControl(task.effort);
  const recur = recurrenceControl(task.recurrence_rule, task.recurrence);

  const form = el("form", {
    class: "edit",
    onsubmit: async (e) => {
      e.preventDefault();
      const changes = {
        description: text.value,
        due: dueField.value || null,
        priority: prio.value,
      };
      if (features.effort !== false) changes.effort = effort.read();
      if (features.snooze !== false) changes.start = start.value || null;
      if (features.recurrence !== false) changes.recurrence = recur.read();
      await patch(task, changes);
      stop();
    },
  }, [
    text, dueField, prio,
    features.effort !== false ? effort.node : null,
    features.snooze !== false ? start : null,
    features.recurrence !== false ? recur.node : null,
    el("button", { type: "submit", text: "Speichern" }),
    el("button", { type: "button", class: "icon-btn", text: "Rohzeile",
      title: "Die Markdown-Zeile direkt bearbeiten",
      onclick: () => { state.editRaw = true; renderList(); } }),
    el("button", { type: "button", class: "icon-btn", text: "Abbrechen", onclick: stop }),
  ]);
  form.addEventListener("keydown", (e) => { if (e.key === "Escape") stop(); });
  setTimeout(() => text.focus(), 0);
  return form;
}

// --- Eisenhower-Matrix -----------------------------------------------
const SVG_NS = "http://www.w3.org/2000/svg";
let scales = null;
let drag = null;

const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  return node;
};

/**
 * Geometrie aus der gemessenen Flaeche. Die viewBox entspricht genau den
 * CSS-Pixeln, also skaliert die Schrift NICHT mit dem Fenster: das
 * Diagramm fuellt immer die ganze Flaeche und bleibt lesbar. Wird es eng,
 * fallen Beschriftungen gestaffelt weg - das ist "Inhalte werden kleiner"
 * fuer ein Diagramm, denn geschrumpfte Schrift waere wertlos.
 */
function geometry() {
  const svg = $("#matrix");
  const rect = svg.getBoundingClientRect();
  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  if (w < 40 || h < 40) return null;            // Ansicht nicht sichtbar

  const narrow = w < 560;
  const short = h < 340;
  const tiny = w < 360 || h < 190;
  // Abbaureihenfolge: zuerst gehen die Achsentexte, dann die
  // Quadrantennamen, erst zuletzt die Punktbeschriftungen - die tragen
  // die meiste Information und sollen am laengsten bleiben.
  return {
    w, h,
    left: narrow ? 26 : 58,
    right: narrow ? 12 : 24,
    top: short ? 10 : 22,
    bottom: short ? 22 : 46,
    axisLabels: !narrow && !short,
    quadLabels: !tiny && w >= 420 && h >= 230,
    maxLabels: tiny ? 0 : (h < 260 ? 6 : (narrow || short ? 12 : 60)),
    radius: tiny ? 4 : (short ? 5 : 6),
    maxChars: narrow ? 16 : (w < 760 ? 24 : 34),
  };
}

function renderMatrix() {
  const svg = $("#matrix");
  const g = geometry();
  if (!g) return;
  svg.setAttribute("viewBox", `0 0 ${g.w} ${g.h}`);
  svg.replaceChildren();

  const { tasks } = visibleTasks();
  const th = state.data.thresholds;

  // Achsenbereich folgt den Daten, haelt die Trennlinien aber immer
  // deutlich im Bild. Feste Untergrenzen wuerden bei wenigen Aufgaben
  // den halben Plot leer lassen.
  const us = tasks.map((t) => t.urgency);
  const is = tasks.map((t) => t.importance);
  const xMax = Math.max(...us, th.urgency * 1.35, 6) * 1.12;
  const yMin = Math.min(...is, th.importance - 1.5, 0) - 0.6;
  const yMax = Math.max(...is, th.importance + 1.5, 5) + 0.6;

  const plotW = g.w - g.left - g.right;
  const plotH = g.h - g.top - g.bottom;
  const px = (u) => g.left + (u / xMax) * plotW;
  const py = (i) => g.h - g.bottom - ((i - yMin) / (yMax - yMin)) * plotH;
  scales = {
    toUrgency: (x) => clamp(((x - g.left) / plotW) * xMax, 0, xMax),
    toImportance: (y) => clamp(yMin + ((g.h - g.bottom - y) / plotH) * (yMax - yMin), yMin, yMax),
  };

  const x0 = px(0), x1 = px(xMax), y0 = py(yMin), y1 = py(yMax);
  const xc = px(th.urgency), yc = py(th.importance);

  const quads = [
    ["plan", x0, y1, xc - x0, yc - y1, "Einplanen", x0 + 10, y1 + 16, "start"],
    ["do", xc, y1, x1 - xc, yc - y1, "Sofort erledigen", x1 - 10, y1 + 16, "end"],
    ["drop", x0, yc, xc - x0, y0 - yc, "Zurückstellen", x0 + 10, y0 - 8, "start"],
    ["delegate", xc, yc, x1 - xc, y0 - yc, "Nebenbei abräumen", x1 - 10, y0 - 8, "end"],
  ];
  for (const [key, rx, ry, rw, rh, label, tx, ty, anchor] of quads) {
    svg.append(svgEl("rect", {
      x: rx, y: ry, width: Math.max(rw, 0), height: Math.max(rh, 0),
      fill: QUAD_COLOR[key], opacity: 0.05,
    }));
    if (g.quadLabels) {
      svg.append(svgEl("text", {
        class: "quad-label", x: tx, y: ty, "text-anchor": anchor,
        fill: QUAD_COLOR[key], text: label,
      }));
    }
  }

  svg.append(svgEl("line", { class: "axis", x1: x0, y1: y0, x2: x1, y2: y0 }));
  svg.append(svgEl("line", { class: "axis", x1: x0, y1: y0, x2: x0, y2: y1 }));
  svg.append(svgEl("line", { class: "axis", "stroke-dasharray": "4 4", x1: xc, y1, x2: xc, y2: y0 }));
  svg.append(svgEl("line", { class: "axis", "stroke-dasharray": "4 4", x1: x0, y1: yc, x2: x1, y2: yc }));
  if (g.axisLabels) {
    svg.append(svgEl("text", {
      class: "axis-label", x: (x0 + x1) / 2, y: g.h - 10, "text-anchor": "middle",
      text: "Dringlichkeit  →  (Fälligkeit + Alter)",
    }));
    const my = (y0 + y1) / 2;
    svg.append(svgEl("text", {
      class: "axis-label", x: 14, y: my, "text-anchor": "middle",
      transform: `rotate(-90 14 ${my})`,
      text: "Wichtigkeit  →  (Priorität + Tags)",
    }));
  }

  // Punkte nach Score absteigend: die wichtigsten bekommen zuerst Platz
  // fuer ihre Beschriftung, und bei Platzmangel bleiben sie beschriftet.
  const ordered = [...tasks].sort((a, b) => b.score - a.score);
  // Alle Punktlagen vorab, damit eine Beschriftung auch FREMDEN Punkten
  // ausweichen kann und nicht nur anderen Beschriftungen.
  const points = ordered.map((task) => ({
    cx: px(task.urgency),
    cy: py(task.importance),
    r: task.done ? g.radius - 2 : g.radius,
  }));
  const placed = [];

  ordered.forEach((task, index) => {
    const { cx, cy } = points[index];
    const dot = svgEl("g", { class: `dot${state.selected === task.uid ? " sel" : ""}` });
    dot.append(svgEl("title", { text: `${task.description}\n\n${breakdownText(task)}` }));
    dot.append(svgEl("circle", {
      cx, cy, r: task.done ? g.radius - 2 : g.radius,
      fill: QUAD_COLOR[task.quadrant],
      opacity: task.done ? 0.35 : (task.snoozed ? 0.45 : 0.9),
      "stroke-dasharray": task.snoozed ? "2 2" : null,
    }));

    if (index < g.maxLabels) {
      const label = task.description.length > g.maxChars
        ? task.description.slice(0, g.maxChars - 1) + "…"
        : task.description;
      const spot = placeLabel(label, cx, cy, g, points, placed);
      if (spot) {
        placed.push(spot.box);
        dot.append(svgEl("text", {
          x: spot.x, y: spot.y, "text-anchor": spot.anchor,
          opacity: task.done ? 0.4 : 1, text: label,
        }));
      }
    }

    dot.addEventListener("pointerdown", (e) => startDrag(e, task, dot));
    svg.append(dot);
  });

  renderDetail(tasks.find((t) => t.uid === state.selected));
}

const overlaps = (a, b) =>
  a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/**
 * Sucht eine freie Lage fuer eine Punktbeschriftung: bevorzugt rechts
 * neben dem Punkt, sonst links, sonst versetzt nach oben oder unten.
 * Hindernisse sind alle Punkte UND alle bereits gesetzten Beschriftungen.
 * Findet sich nichts, bleibt der Punkt unbeschriftet - besser als
 * uebereinandergedruckter Text.
 */
function placeLabel(text, cx, cy, g, points, placed) {
  const width = text.length * 5.6;          // Naeherung fuer 11px sans
  const obstacles = points.map((p) => ({
    x0: p.cx - p.r - 2, x1: p.cx + p.r + 2, y0: p.cy - p.r - 2, y1: p.cy + p.r + 2,
  }));
  const sides = cx < g.w * 0.72 ? ["start", "end"] : ["end", "start"];

  for (const dy of [4, -9, 16, -21, 28, -33]) {
    for (const anchor of sides) {
      const x = anchor === "start" ? cx + 10 : cx - 10;
      const box = {
        x0: anchor === "start" ? x : x - width,
        x1: anchor === "start" ? x + width : x,
        y0: cy + dy - 9, y1: cy + dy + 3,
      };
      if (box.x0 < g.left || box.x1 > g.w - g.right) continue;
      if (box.y0 < g.top || box.y1 > g.h - g.bottom) continue;
      if (obstacles.some((o) => overlaps(box, o))) continue;
      if (placed.some((o) => overlaps(box, o))) continue;
      return { x, y: cy + dy, anchor, box };
    }
  }
  return null;
}

function toSvgPoint(svg, e) {
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  return new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
}

function startDrag(e, task, node) {
  if (e.button !== undefined && e.button !== 0) return;
  const svg = $("#matrix");
  drag = { task, node, start: toSvgPoint(svg, e), moved: false };
  node.classList.add("dragging");
  try { svg.setPointerCapture(e.pointerId); } catch { /* ohne Capture gehts auch */ }
  e.preventDefault();
}

function initMatrixDrag() {
  const svg = $("#matrix");

  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = toSvgPoint(svg, e);
    const dx = p.x - drag.start.x;
    const dy = p.y - drag.start.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true;
    if (!isReadOnly()) drag.node.setAttribute("transform", `translate(${dx} ${dy})`);
  });

  svg.addEventListener("pointerup", async (e) => {
    if (!drag) return;
    const { task, node, moved } = drag;
    drag = null;
    node.classList.remove("dragging");
    state.selected = task.uid;

    if (!moved || isReadOnly()) { renderMatrix(); return; }
    const p = toSvgPoint(svg, e);
    try {
      await patch(task, { urgency: scales.toUrgency(p.x), importance: scales.toImportance(p.y) });
    } catch {
      renderMatrix();
    }
  });

  svg.addEventListener("pointercancel", () => {
    if (!drag) return;
    drag.node.classList.remove("dragging");
    drag = null;
    renderMatrix();
  });

  svg.addEventListener("dblclick", () => {
    const task = (state.data?.tasks || []).find((t) => t.uid === state.selected);
    if (task) toggleDone(task);
  });

  // Die Matrix haengt an der gemessenen Flaeche, also auf jede
  // Groessenaenderung neu zeichnen - auch wenn nicht das Fenster, sondern
  // nur die Kopfzeile umgebrochen ist.
  let pending = false;
  new ResizeObserver(() => {
    if (state.view !== "matrix" || !state.data || pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; renderMatrix(); });
  }).observe($(".matrix-wrap"));
}

function renderDetail(task) {
  const box = $("#matrix-detail");
  box.replaceChildren();
  if (!task) {
    box.textContent = isReadOnly()
      ? "Zeitreise aktiv – Punkte zeigen den simulierten Stand, Ziehen ist gesperrt."
      : "Punkt anklicken · ziehen ändert Frist (waagerecht) und Priorität (senkrecht) · Doppelklick hakt ab.";
    return;
  }
  const due = dueInfo(task, state.data.today);
  box.append(...[
    el("b", { text: task.description }),
    document.createTextNode(
      ` — Score ${task.score} = Dringlichkeit ${task.urgency} + Wichtigkeit ${task.importance}`
      + (due ? ` · fällig ${task.due} (${due.text})` : " · kein Datum")
      + (task.effort_text ? ` · Aufwand ${task.effort_text}, steigt ab ${task.horizon_days} d vorher` : "")
      + (task.snoozed ? ` · zurückgestellt bis ${task.start}` : "")
      + (task.recurrence_text ? ` · ${task.recurrence_text}` : "")
      + (task.tags.length ? ` · #${task.tags.join(" #")}` : "")
      + " ",
    ),
    isReadOnly() ? null : el("button", {
      class: "icon-btn", text: task.done ? "↩ wieder öffnen" : "✓ abhaken",
      onclick: () => toggleDone(task),
    }),
  ].filter(Boolean));
}

// --- Gewichte: einfacher Modus ---------------------------------------
const SIMPLE_SPEC = [
  ["Formel", [
    {
      label: "Wie stark zieht eine Frist?", ends: ["egal", "sehr stark"],
      read: (c, d) => c.due.coefficient / (d.due.coefficient / 5),
      write: (n, c, d) => { c.due.coefficient = round2(n * (d.due.coefficient / 5)); },
    },
    {
      label: "Wie stark zählt die Priorität?", ends: ["egal", "sehr stark"],
      read: (c, d) => c.priority.highest / (d.priority.highest / 5),
      write: (n, c, d) => {
        const f = n / 5;
        for (const k of PRIO_KEYS) c.priority[k] = round2(d.priority[k] * f);
      },
      exact: (c, d) => {
        const f = c.priority.highest / d.priority.highest;
        return PRIO_KEYS.every((k) => Math.abs(c.priority[k] - d.priority[k] * f) < 0.01);
      },
    },
    {
      label: "Wie stark zählt das Liegenlassen?", ends: ["egal", "sehr stark"],
      read: (c, d) => c.age.coefficient / (d.age.coefficient / 5),
      write: (n, c, d) => { c.age.coefficient = round2(n * (d.age.coefficient / 5)); },
    },
    {
      label: "Wie viel Vorlauf braucht große Arbeit?", ends: ["keinen", "sehr viel"],
      read: (c, d) => c.effort.days_per_hour / (d.effort.days_per_hour / 5),
      write: (n, c, d) => { c.effort.days_per_hour = round2(n * (d.effort.days_per_hour / 5)); },
    },
    {
      label: "Bonus für zugeordnete Projekte", ends: ["keiner", "groß"],
      read: (c, d) => c.tags.coefficient / (d.tags.coefficient / 5),
      write: (n, c, d) => { c.tags.coefficient = round2(n * (d.tags.coefficient / 5)); },
    },
    {
      label: "Wie viele Tage vorher wird es dringend?", unit: "Tage", min: 0, max: 60,
      read: (c) => c.due.future_days_for_min,
      write: (n, c) => { c.due.future_days_for_min = n; },
    },
  ]],
  ["Matrix", [
    {
      label: "Trennlinie Dringlichkeit", min: 0, max: 20,
      read: (c) => c.thresholds.urgency,
      write: (n, c) => { c.thresholds.urgency = n; },
    },
    {
      label: "Trennlinie Wichtigkeit", min: -2, max: 12,
      read: (c) => c.thresholds.importance,
      write: (n, c) => { c.thresholds.importance = n; },
    },
  ]],
];

/** Regler des Erweitert-Modus. Letztes Feld: übliche Stellschraube. */
const ADVANCED_SPEC = [
  ["Fälligkeit", [
    ["due", "coefficient", "Wirkung insgesamt", 0, 24, 0.5, true],
    ["due", "future_days_for_min", "Vorlauf in Tagen", 0, 60, 1, true],
    ["due", "overdue_days_for_max", "Tage überfällig bis Maximum", 0, 30, 1, false],
    ["due", "min_factor", "Grundanteil weit vor der Frist", 0, 1, 0.05, false],
  ]],
  // Spanne weit genug, damit auch der Einfach-Modus bei 10 hineinpasst.
  ["Priorität", PRIO_KEYS.map((k) =>
    ["priority", k, k === "none" ? "ohne Marker" : `${PRIO[k]} ${PRIO_LABEL[k].split(" ")[1]}`,
     -6, 20, 0.1, k === "highest" || k === "none"])],
  ["Aufwand, Alter, Projekte", [
    ["effort", "days_per_hour", "Tage Vorlauf je Stunde Aufwand", 0, 5, 0.1, true],
    ["effort", "max_extra_days", "Deckel dieser Verschiebung", 0, 120, 5, false],
    ["age", "coefficient", "Wirkung des Alters", 0, 8, 0.1, true],
    ["age", "max_days", "Alter bis Maximum (Tage)", 30, 1095, 5, false],
    ["tags", "coefficient", "Bonus für #Projekt", 0, 4, 0.1, false],
  ]],
  ["Matrix-Schwellen", [
    ["thresholds", "urgency", "Trennlinie Dringlichkeit", 0, 20, 0.5, true],
    ["thresholds", "importance", "Trennlinie Wichtigkeit", -2, 12, 0.5, true],
  ]],
];

async function loadConfig() {
  const cfg = await api("/api/config");
  state.saved = cfg.urgency;
  state.defaults = cfg.defaults;
  state.weights = structuredClone(cfg.urgency);
  renderWeights();
}

function renderWeights() {
  const body = $("#weights-body");
  body.replaceChildren();
  $("#weights").classList.toggle("simple", state.weightsMode === "simple");
  $("#mode-simple").classList.toggle("is-active", state.weightsMode === "simple");
  $("#mode-advanced").classList.toggle("is-active", state.weightsMode !== "simple");
  if (!state.weights) return;
  if (state.weightsMode === "simple") renderSimple(body);
  else renderAdvanced(body);
  updateWeightsHead();
}

function renderSimple(body) {
  const c = state.weights, d = state.defaults;
  let anyInexact = false;

  for (const [groupLabel, entries] of SIMPLE_SPEC) {
    body.append(el("div", { class: "weights-group", text: groupLabel }));
    for (const entry of entries) {
      const raw = entry.read(c, d);
      const n = Math.round(raw);
      const exact = Math.abs(raw - n) < 0.01 && (!entry.exact || entry.exact(c, d));
      if (!exact) anyInexact = true;

      const min = entry.min ?? 0;
      const max = entry.max ?? 10;
      const out = el("output", { text: entry.unit ? `${n} ${entry.unit}` : String(n) });
      const range = el("input", {
        type: "range", min, max, step: 1, value: clamp(n, min, max),
        oninput: (e) => {
          const v = Number(e.target.value);
          entry.write(v, state.weights, state.defaults);
          out.textContent = entry.unit ? `${v} ${entry.unit}` : String(v);
          refreshWeights();
        },
      });
      const track = el("div", { class: "track" }, [
        range,
        entry.ends ? el("div", { class: "scale-ends" }, [
          el("span", { text: entry.ends[0] }), el("span", { text: entry.ends[1] }),
        ]) : null,
      ]);
      body.append(el("div", { class: "slider" }, [el("span", { text: entry.label }), track, out]));
    }
  }

  $("#weights-note").textContent = anyInexact
    ? "Einzelne Werte sind im Erweitert-Modus feiner eingestellt, als diese Regler darstellen "
      + "können. Schieben hier überschreibt sie. Bei 5 steht alles auf dem Auslieferungswert."
    : "Bei 5 steht ein Regler auf dem Auslieferungswert. Erst „Übernehmen“ schreibt in die "
      + "config.toml – Kommentare dort bleiben erhalten.";
}

function renderAdvanced(body) {
  for (const [groupLabel, rows] of ADVANCED_SPEC) {
    body.append(el("div", { class: "weights-group", text: groupLabel }));
    for (const [section, key, label, min, max, step, common] of rows) {
      const current = state.weights[section]?.[key];
      if (current === undefined) continue;
      const out = el("output", { text: fmt(current) });
      const range = el("input", {
        type: "range", min, max, step, value: current,
        oninput: (e) => {
          state.weights[section][key] = Number(e.target.value);
          out.textContent = fmt(state.weights[section][key]);
          markChanged(wrap, section, key);
          refreshWeights();
        },
      });
      const wrap = el("div", { class: `slider${common ? " common" : ""}` }, [
        el("span", { text: label, title: common ? "Übliche Stellschraube" : "" }), range, out,
      ]);
      markChanged(wrap, section, key);
      body.append(wrap);
    }
  }
  $("#weights-note").textContent =
    "★ markiert die Stellschrauben, an denen man üblicherweise dreht. Erst „Übernehmen“ "
    + "schreibt in die config.toml – Kommentare dort bleiben erhalten.";
}

const fmt = (v) => (Number.isInteger(v) ? String(v) : String(round2(v)));

function markChanged(wrap, section, key) {
  const same = state.saved?.[section]?.[key] === state.weights?.[section]?.[key];
  wrap.classList.toggle("changed", !same);
}

let weightsTimer = null;
function refreshWeights() {
  updateWeightsHead();
  clearTimeout(weightsTimer);
  weightsTimer = setTimeout(() => load().catch(() => {}), 150);
}

function updateWeightsHead() {
  const dirty = weightsDirty();
  $("#weights-save").disabled = !dirty;
  $("#weights-state").textContent = dirty ? "verstellt – Vorschau aktiv" : "gespeicherter Stand";
}

function setWeightsOpen(open) {
  state.weightsOpen = open;
  $("#weights").hidden = !open;
  $("#weights-toggle").classList.toggle("is-active", open);
  if (open && !state.weights) loadConfig().catch(() => {});
}

function initWeights() {
  $("#weights-toggle").addEventListener("click", () => setWeightsOpen(!state.weightsOpen));
  $("#weights-close").addEventListener("click", () => setWeightsOpen(false));
  $("#mode-simple").addEventListener("click", () => { state.weightsMode = "simple"; renderWeights(); });
  $("#mode-advanced").addEventListener("click", () => { state.weightsMode = "advanced"; renderWeights(); });

  $("#weights-save").addEventListener("click", async () => {
    const cfg = await api("/api/config", {
      method: "PUT", body: JSON.stringify({ urgency: state.weights }),
    });
    state.saved = cfg.urgency;
    state.weights = structuredClone(cfg.urgency);
    setStatus("Gewichte gespeichert.");
    renderWeights();
    await load();
  });

  $("#weights-reset").addEventListener("click", () => {
    if (!state.defaults) return;
    state.weights = structuredClone(state.defaults);
    renderWeights();
    refreshWeights();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.weightsOpen) setWeightsOpen(false);
  });
}

// --- Zeitreise -------------------------------------------------------
let timeTimer = null;
function initTimeTravel() {
  const slider = $("#time-slider");
  const label = $("#time-label");
  slider.addEventListener("input", () => {
    state.timeOffset = Number(slider.value);
    const d = new Date();
    d.setDate(d.getDate() + state.timeOffset);
    label.textContent = state.timeOffset === 0
      ? "heute"
      : `in ${state.timeOffset} ${state.timeOffset === 1 ? "Tag" : "Tagen"} (${d.toLocaleDateString("de-DE")})`;
    clearTimeout(timeTimer);
    timeTimer = setTimeout(() => load().catch(() => {}), 120);
  });
}

// --- Verdrahtung -----------------------------------------------------
function setView(name, remember = true) {
  state.view = name;
  state.editing = null;
  // Ansicht in der Adresse merken: uebersteht einen Reload und macht die
  // Matrix direkt verlinkbar.
  if (remember && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  document.querySelectorAll(".tabs .tab").forEach((t) =>
    t.classList.toggle("is-active", t.dataset.view === name));
  $("#view-list").hidden = name !== "list";
  $("#view-matrix").hidden = name !== "matrix";
  // Steuerelemente, die nur zu einer Ansicht gehoeren, verschwinden ganz,
  // statt in der anderen Platz zu verbrauchen.
  for (const node of document.querySelectorAll("[data-view-only]")) {
    node.classList.toggle("is-off", node.dataset.viewOnly !== name);
  }
  render(state.data);
}

for (const tab of document.querySelectorAll(".tabs .tab")) {
  tab.addEventListener("click", () => setView(tab.dataset.view));
}

const bindToggle = (id, field) => $(id).addEventListener("change", (e) => {
  state[field] = e.target.checked;
  render(state.data);
});
bindToggle("#show-done", "showDone");
bindToggle("#show-snoozed", "showSnoozed");
bindToggle("#group-tags", "groupTags");

$("#effort-filter").addEventListener("change", (e) => {
  state.effortLimit = e.target.value;
  render(state.data);
});

let addEffort, addRecur;
function resetAddControls() {
  addEffort = effortControl(null);
  addRecur = recurrenceControl(null, null);
  $("#add-effort").replaceChildren(addEffort.node);
  $("#add-recurrence").replaceChildren(addRecur.node);
}
resetAddControls();

$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/tasks", {
    method: "POST",
    body: JSON.stringify({
      description: $("#add-text").value,
      due: $("#add-due").value || null,
      priority: $("#add-priority").value,
      effort: addEffort.read() || null,
      start: $("#add-start").value || null,
      recurrence: addRecur.read() || null,
    }),
  });
  $("#add-text").value = "";
  $("#add-due").value = "";
  $("#add-start").value = "";
  $("#add-priority").value = "none";
  resetAddControls();
  await load();
  offerUndo();
  $("#add-text").focus();
});

$("#undo").addEventListener("click", doUndo);
$("#redo").addEventListener("click", doRedo);

const inTextField = (node) =>
  node && (["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName) || node.isContentEditable);

document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  const key = e.key.toLowerCase();
  if (key !== "z" && key !== "y") return;
  // In einem Eingabefeld hat das Rückgängig des Browsers Vorrang.
  if (inTextField(e.target)) return;
  e.preventDefault();
  if (key === "y" || e.shiftKey) doRedo();
  else doUndo();
});

initMatrixDrag();
initWeights();
initTimeTravel();
setView(location.hash === "#matrix" ? "matrix" : "list");

setInterval(() => fetch("/api/heartbeat", { method: "POST" }).catch(() => {}), 30000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.editing && !drag) load().catch(() => {});
});

load().catch(() => {});

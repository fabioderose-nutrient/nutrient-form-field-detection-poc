// Review the proposed fields in the Nutrient Web SDK Form Creator, then export a fillable PDF.

const SDK_BASE = "https://cdn.cloud.pspdfkit.com/pspdfkit-web@1.24.0/";
const state = { config: {}, forms: [], current: null, proposals: null, byName: {}, instance: null, rows: [], refreshTimer: null };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const titleOf = (id) => id.replace(/^upload-/, "").replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase());

// ---------------------------------------------------------------- start

async function start() {
  state.config = await fetch("/api/config").then((r) => r.json());
  $("meta").textContent = "Fields found by the Nutrient SDK form detection model" +
    (state.config.llm ? `, named with ${state.config.llm}` : ", named by layout rules");
  await refreshForms();
  $("forms").addEventListener("click", (e) => {
    const b = e.target.closest("[data-form]");
    if (b) openForm(b.dataset.form);
  });
  $("upload").addEventListener("change", upload);
  $("export").addEventListener("click", exportPdf);
  $("fields").addEventListener("click", (e) => {
    const row = e.target.closest("[data-index]");
    if (row) focusField(state.rows[Number(row.dataset.index)]);
  });
  const wanted = new URLSearchParams(location.search).get("form");
  const first = state.forms.find((f) => f.id === wanted) || state.forms[0];
  if (first) openForm(first.id);
}

async function refreshForms() {
  state.forms = await fetch("/api/forms").then((r) => r.json());
  $("forms").innerHTML = state.forms.map((f) => `
    <button class="form-item${f.id === state.current ? " active" : ""}" data-form="${esc(f.id)}">
      <span>${esc(titleOf(f.id))}</span><small>${f.fields == null ? "new" : `${f.fields} fields`}</small>
    </button>`).join("");
}

async function upload(e) {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const res = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
  if (!res.ok) { setStatus(await res.text()); return; }
  const { id } = await res.json();
  await refreshForms();
  openForm(id);
}

function setStatus(text) { $("viewer-status").textContent = text; }

// ---------------------------------------------------------------- open a form with its proposals

async function openForm(id) {
  const NV = window.NutrientViewer;
  state.current = id;
  document.querySelectorAll(".form-item").forEach((b) => b.classList.toggle("active", b.dataset.form === id));
  $("export").disabled = true;
  $("export-result").hidden = true;
  $("fields").innerHTML = "";
  $("field-count").textContent = "";
  $("viewer-title").textContent = titleOf(id);
  const known = state.forms.find((f) => f.id === id)?.fields != null;
  setStatus(known ? "Loading..." : "Detecting fields: the SDK model, then the local LLM for the names (about 30 s per page)...");
  $("summary").textContent = known ? "" : "Detecting...";

  const res = await fetch(`/api/forms/${encodeURIComponent(id)}/proposals`);
  if (!res.ok) { setStatus("Detection failed"); $("summary").textContent = await res.text(); return; }
  const proposals = await res.json();
  if (state.current !== id) return;
  state.proposals = proposals;
  state.byName = Object.fromEntries(proposals.fields.map((f) => [f.name, f]));
  renderSummary(proposals);
  if (!known) refreshForms();

  if (state.instance) { NV.unload(state.instance); state.instance = null; }
  $("viewer-empty").hidden = true;
  const instance = await NV.load({
    container: "#viewer",
    document: `/api/forms/${encodeURIComponent(id)}/pdf`,
    baseUrl: SDK_BASE,
    licenseKey: state.config.web_sdk_license_key || undefined,
    toolbarItems: [
      ...NV.defaultToolbarItems.filter((i) => ["sidebar-thumbnails", "pager", "zoom-out", "zoom-in", "zoom-mode"].includes(i.type)),
      { type: "spacer" },
      { type: "form-creator" },
    ],
    initialViewState: new NV.ViewState({ zoom: NV.ZoomMode.FIT_TO_WIDTH, interactionMode: NV.InteractionMode.FORM_CREATOR }),
  });
  if (state.current !== id) { NV.unload(instance); return; }
  state.instance = instance;
  await createFields(instance, proposals.fields);
  for (const event of ["formFields.change", "formFields.create", "formFields.delete", "annotations.change"]) {
    instance.addEventListener(event, scheduleRefresh);
  }
  await refreshFieldList();
  $("export").disabled = false;
  setStatus(`${proposals.fields.length} proposed fields loaded in Form Creator mode`);
}

function renderSummary(p) {
  const count = (key) => p.fields.reduce((m, f) => ({ ...m, [f[key]]: (m[f[key]] || 0) + 1 }), {});
  const rows = (obj) => Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<tr><td>${esc(k)}</td><td>${n}</td></tr>`).join("");
  const sources = { model: "SDK model", geometry: "geometry", merged: "merged ruled lines" };
  const bySource = Object.fromEntries(Object.entries(count("source")).map(([k, n]) => [sources[k] || k, n]));
  const t = p.timings || {};
  $("summary").innerHTML = `
    <table>${rows(count("type"))}</table>
    <div class="side-title">Found by</div>
    <table>${rows(bySource)}</table>
    <div class="note">Detection ${(t.model ?? 0).toFixed(1)} s, geometry ${(t.geometry ?? 0).toFixed(1)} s,
      naming ${(t.naming ?? 0).toFixed(1)} s${p.settings?.llm ? ` (${esc(p.settings.llm)})` : ""}.</div>`;
}

// Each proposal becomes a widget annotation and a form field, as if it had been drawn in the Form Creator.
async function createFields(instance, fields) {
  const NV = window.NutrientViewer;
  const { List } = NV.Immutable;
  const items = [];
  for (const f of fields) {
    const id = NV.generateInstantId();
    const [x0, y0, x1, y1] = f.rect;
    const widget = new NV.Annotations.WidgetAnnotation({
      id,
      pageIndex: f.page - 1,
      formFieldName: f.name,
      boundingBox: new NV.Geometry.Rect({ left: x0, top: y0, width: x1 - x0, height: y1 - y0 }),
      ...(f.type === "date" ? { additionalActions: { onFormat: new NV.Actions.JavaScriptAction({ script: 'AFDate_FormatEx("dd/mm/yyyy");' }) } } : {}),
    });
    let field;
    if (f.type === "checkbox") {
      field = new NV.FormFields.CheckBoxFormField({
        name: f.name, label: f.label, annotationIds: List([id]),
        options: List([new NV.FormOption({ label: "Yes", value: "Yes" })]), defaultValues: List([]),
      });
    } else if (f.type === "signature") {
      field = new NV.FormFields.SignatureFormField({ name: f.name, label: f.label, annotationIds: List([id]) });
    } else {
      field = new NV.FormFields.TextFormField({ name: f.name, label: f.label, annotationIds: List([id]), multiLine: f.type === "multiline" });
    }
    items.push(widget, field);
  }
  if (items.length) await instance.create(items);
}

// ---------------------------------------------------------------- field list, kept in sync with the editor

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refreshFieldList, 250);
}

function typeOf(field) {
  const NV = window.NutrientViewer;
  if (field instanceof NV.FormFields.CheckBoxFormField) return "checkbox";
  if (field instanceof NV.FormFields.SignatureFormField) return "signature";
  if (field instanceof NV.FormFields.TextFormField) {
    const proposed = state.byName[field.name]?.type;
    if (proposed && proposed !== "checkbox" && proposed !== "signature") return field.multiLine ? "multiline" : proposed;
    return field.multiLine ? "multiline" : "text";
  }
  return field.constructor?.name?.replace("FormField", "").toLowerCase() || "field";
}

async function refreshFieldList() {
  const NV = window.NutrientViewer;
  const instance = state.instance;
  if (!instance) return;
  const widgets = new Map();
  for (let p = 0; p < instance.totalPageCount; p++) {
    (await instance.getAnnotations(p)).forEach((a) => { if (a instanceof NV.Annotations.WidgetAnnotation) widgets.set(a.id, a); });
  }
  const formFields = await instance.getFormFields();
  state.rows = formFields.toArray().map((f) => {
    const w = widgets.get(f.annotationIds.first());
    const proposed = state.byName[f.name];
    return { name: f.name, label: f.label || proposed?.label || "", type: typeOf(f), widget: w,
             source: proposed ? (proposed.source === "model" ? `model ${Math.round(proposed.confidence * 100)}%` : proposed.source) : "added or renamed" };
  }).filter((r) => r.widget)
    .sort((a, b) => a.widget.pageIndex - b.widget.pageIndex
      || Math.round(a.widget.boundingBox.top / 8) - Math.round(b.widget.boundingBox.top / 8)
      || a.widget.boundingBox.left - b.widget.boundingBox.left);

  let page = -1;
  const html = [];
  state.rows.forEach((r, i) => {
    if (r.widget.pageIndex !== page && instance.totalPageCount > 1) { page = r.widget.pageIndex; html.push(`<div class="page-sep">Page ${page + 1}</div>`); }
    html.push(`<div class="field" data-index="${i}">
      <div class="field-row"><span class="field-name">${esc(r.name)}</span><span class="type ${esc(r.type)}">${esc(r.type)}</span></div>
      <div class="field-row"><span class="field-label">${esc(r.label)}</span><span class="source">${esc(r.source)}</span></div>
    </div>`);
  });
  $("fields").innerHTML = html.join("");
  $("field-count").textContent = `(${state.rows.length})`;
}

function focusField(row) {
  const NV = window.NutrientViewer;
  if (!row || !state.instance) return;
  document.querySelectorAll(".field.active").forEach((el) => el.classList.remove("active"));
  document.querySelector(`.field[data-index="${state.rows.indexOf(row)}"]`)?.classList.add("active");
  state.instance.setSelectedAnnotations(NV.Immutable.List([row.widget.id]));
  state.instance.jumpToRect(row.widget.pageIndex, row.widget.boundingBox);
}

// ---------------------------------------------------------------- export

async function exportPdf() {
  const instance = state.instance;
  if (!instance) return;
  $("export").disabled = true;
  const box = $("export-result");
  try {
    const pdf = await instance.exportPDF();
    const res = await fetch(`/api/forms/${encodeURIComponent(state.current)}/export`, {
      method: "POST", headers: { "Content-Type": "application/pdf" }, body: pdf,
    });
    if (!res.ok) throw new Error(await res.text());
    const info = await res.json();
    box.className = "export-result" + (info.acroform && info.count ? "" : " bad");
    box.innerHTML = info.acroform
      ? `Fillable PDF saved. Read back with pypdf: an AcroForm with ${info.count} fields. ` +
        `<a href="/api/forms/${encodeURIComponent(state.current)}/fillable.pdf">Download</a>`
      : "The exported PDF has no AcroForm.";
  } catch (err) {
    box.className = "export-result bad";
    box.textContent = `Export failed: ${err.message}`;
  } finally {
    box.hidden = false;
    $("export").disabled = false;
  }
}

start();

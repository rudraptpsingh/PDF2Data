const TOKEN = "__TOKEN__";
const $ = (id) => document.getElementById(id);
const active = (stage) => ["extracting", "embedding"].includes(stage);
let selected = null, job = null, timer = null;
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function choose(file) {
  if (!file) return;
  selected = file;
  $("fileName").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $("title").value = file.name.replace(/\.pdf$/i, "");
  $("extract").disabled = false;
}

$("file").onchange = (event) => choose(event.target.files[0]);
$("drop").ondragover = (event) => { event.preventDefault(); $("drop").classList.add("drag"); };
$("drop").ondragleave = () => $("drop").classList.remove("drag");
$("drop").ondrop = (event) => {
  event.preventDefault(); $("drop").classList.remove("drag"); choose(event.dataTransfer.files[0]);
};

async function api(path, method = "GET", body = null) {
  const isFile = body instanceof Blob;
  const response = await fetch(path, {
    method,
    headers: { "X-Local-Token": TOKEN, ...(isFile ? { "Content-Type": "application/pdf" } : body ? { "Content-Type": "application/json" } : {}) },
    body: body && !isFile ? JSON.stringify(body) : body,
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request failed");
  return result;
}

function statusText(state) {
  if (state.error) return `Error: ${state.error}`;
  if (state.stage === "extracting") return `Extracting page ${state.progress} of ${state.total}…`;
  if (state.stage === "embedding") return `Generating embeddings locally… ${state.vectors || 0} vectors written`;
  if (state.stage === "review_required") return "Review needed: unreadable text, low OCR confidence, or an extraction failure. Review the flagged pages before embedding.";
  if (state.stage === "ready") return "Extraction complete. Review the evidence, then generate embeddings.";
  if (state.stage === "complete") return "Embeddings complete and verified.";
  return "Choose a PDF to begin.";
}

async function loadPreview(number = 1) {
  const preview = await api(`/api/preview?page=${number}`);
  const page = preview.page;
  $("reviewPage").value = String(number);
  $("pageEvidence").textContent = `Page ${page.page} · ${page.method === "pdf_text" ? "native PDF text" : `${page.method} OCR · native extraction preserved in the bundle`}`;
  $("pageDetails").classList.remove("hidden");
  $("pagePreview").textContent = page.text || "No searchable text on this page.";
  $("factsDetails").classList.toggle("hidden", !preview.facts.length);
  $("factPreview").textContent = preview.facts.map((item) => `${item.kind} · ${item.value_text}\n${item.context}`).join("\n\n");
  $("structureDetails").classList.toggle("hidden", !preview.structure.length);
  $("structurePreview").textContent = preview.structure.map((item) => {
    const loc = item.evidence[0];
    const cues = ["operator", "conjunction", "subject", "temporal_scope"].flatMap((kind) =>
      (item[`${kind}_cues`] || []).map((cue) => `${kind}: ${cue.quote}`));
    return `${item.kind}${item.proposed_role ? ` · ${item.proposed_role}` : ""}${item.table_id ? ` · row ${item.row + 1}, column ${item.column + 1}` : ""}\n${loc ? `p${loc.page} @${loc.char_start}: ${loc.quote}` : "No unique page-text span; inspect PDF table"}${cues.length ? `\n${cues.join(" · ")}` : ""}`;
  }).join("\n\n");
  $("tableDetails").classList.toggle("hidden", !preview.tables.length);
  $("tablePreview").innerHTML = preview.tables.map((table) => `<p>${safe(table.id)}</p><table>${table.cells.map((row) =>
    `<tr>${row.map((cell) => `<td>${safe(cell)}</td>`).join("")}</tr>`).join("")}</table>`).join("");
}
$("reviewPage").onchange = () => loadPreview(Number($("reviewPage").value)).catch(showError);

function showError(error) { $("status").textContent = error.message; $("status").className = "status issue"; }
function startPolling() { if (!timer) timer = setInterval(refresh, 850); }

async function refresh() {
  try {
    const state = await api("/api/status");
    $("status").textContent = statusText(state);
    $("status").className = `status ${state.error || state.stage === "review_required" ? "issue" : state.stage === "complete" ? "ok" : ""}`;
    $("extract").disabled = active(state.stage) || !selected;
    $("embed").disabled = !["ready", "complete"].includes(state.stage);
    $("download").disabled = !state.report || active(state.stage);
    if (state.report) {
      const report = state.report;
      const metrics = [["Pages", report.pages], ["Text characters", report.text_chars.toLocaleString()], ["Tables", report.tables], ["Value candidates", report.numeric_candidates], ["Structure cues", report.structure_candidates || 0]];
      if (state.vectors) metrics.push(["Vectors", state.vectors]);
      $("metrics").innerHTML = metrics.map(([label, value]) => `<div><b>${safe(value)}</b><span>${label}</span></div>`).join("");
      $("issues").innerHTML = report.issues.length ? `<p class="issue">${report.issues.length} review note(s): ${safe(report.issues.slice(0, 6).map((item) => `p${item.page} ${item.kind}`).join(", "))}</p>` : "";
      if (job !== report.document_id) {
        job = report.document_id;
        $("reviewHint").textContent = "Review extracted evidence. Download the bundle before closing this server.";
        $("reviewPage").innerHTML = Array.from({ length: report.pages }, (_, index) => `<option value="${index + 1}">Page ${index + 1}</option>`).join("");
        $("reviewPage").disabled = false;
        await loadPreview();
      }
    }
    if (!active(state.stage)) { clearInterval(timer); timer = null; }
  } catch (error) { showError(error); clearInterval(timer); timer = null; }
}

$("extract").onclick = async () => {
  if (!selected) return;
  $("extract").disabled = true;
  $("status").textContent = "Uploading PDF…";
  try {
    const query = new URLSearchParams({ title: $("title").value, url: $("url").value, tables: $("tables").checked ? "1" : "0", ocr: $("ocr").value, language: $("language").value });
    await api(`/api/upload?${query}`, "POST", selected);
    job = null; startPolling(); await refresh();
  } catch (error) { showError(error); $("extract").disabled = false; }
};

$("embed").onclick = async () => {
  $("embed").disabled = true;
  try {
    await api("/api/embed", "POST", {
      model: $("model").value, backend: $("backend").value, device: $("device").value,
      batch_size: Number($("batch").value), chunk_chars: Number($("window").value),
      overlap: Number($("overlap").value), workers: Number($("workers").value),
      threads_per_worker: Number($("threads").value), max_tokens: Number($("tokens").value),
      pooling: $("pooling").value, offline: $("offline").checked,
    });
    startPolling(); await refresh();
  } catch (error) { showError(error); $("embed").disabled = false; }
};
$("download").onclick = () => { location.href = `/api/download?token=${encodeURIComponent(TOKEN)}`; };
refresh();

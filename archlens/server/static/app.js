/**
 * ArchLens front-end.
 *
 * No build step and no framework: the server is FastAPI-only, so this is a
 * plain ES module. Mermaid is the single external dependency and is imported
 * from a CDN at runtime; if that import fails the flow tab degrades to showing
 * the Mermaid source rather than breaking the page.
 */

/* Mermaid is loaded dynamically rather than with a static import. A static
 * import that fails - offline, blocked CDN, corporate proxy - aborts the whole
 * module, taking the scan form and the architecture diagram down with it. The
 * flowcharts are the only part that actually needs it, so a failure here
 * degrades to showing the Mermaid source and nothing else breaks. */
const MERMAID_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

let mermaid = null;
let mermaidLoad = null;

function loadMermaid() {
  if (mermaidLoad) return mermaidLoad;
  mermaidLoad = import(MERMAID_URL)
    .then((module) => { mermaid = module.default; configureMermaid(); return mermaid; })
    .catch(() => { mermaid = null; return null; });
  return mermaidLoad;
}

function configureMermaid() {
  if (!mermaid) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'loose',
    theme: currentTheme() === 'dark' ? 'dark' : 'default',
    fontFamily: "Inter, 'Segoe UI', sans-serif",
    flowchart: { curve: 'basis', useMaxWidth: true, padding: 16 },
    sequence: { useMaxWidth: true, mirrorActors: false },
  });
}

/* ── Element handles ────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const els = {
  form: $('scanForm'), path: $('pathInput'), scanBtn: $('scanBtn'),
  useLlm: $('useLlm'), llmHint: $('llmHint'), dark: $('darkDiagram'), direction: $('direction'),
  progress: $('progress'), progressBar: $('progressBar'), progressMsg: $('progressMsg'),
  capabilities: $('capabilities'), themeToggle: $('themeToggle'),
  empty: $('emptyState'), errorState: $('errorState'), errorText: $('errorText'),
  results: $('results'), projectName: $('projectName'), projectSummary: $('projectSummary'),
  statChips: $('statChips'), warnings: $('warnings'),
  diagramTabs: $('diagramTabs'), viewer: $('viewer'), stage: $('viewerStage'),
  zoomIn: $('zoomIn'), zoomOut: $('zoomOut'), zoomFit: $('zoomFit'), zoomLevel: $('zoomLevel'),
  downloadPng: $('downloadPng'), downloadSvg: $('downloadSvg'), legend: $('legend'),
  flowList: $('flowList'), componentGrid: $('componentGrid'),
  stackGrid: $('stackGrid'), highlights: $('highlights'), evidenceBody: $('evidenceBody'),
  historyPanel: $('historyPanel'), historyList: $('historyList'),
  browseBtn: $('browseBtn'), browseDialog: $('browseDialog'), browseList: $('browseList'),
  browsePath: $('browsePath'), browseUp: $('browseUp'), browseSelect: $('browseSelect'),
  browseClose: $('browseClose'),
};

const state = {
  jobId: null,
  result: null,
  diagrams: [],
  activeDiagram: 0,
  browseCurrent: '',
  history: [],
  pollTimer: null,
};

/* ── Utilities ──────────────────────────────────────────────────────────── */

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const num = (value) => Number(value ?? 0).toLocaleString();

async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return response.json();
}

/* ── Theme ──────────────────────────────────────────────────────────────── */

function currentTheme() {
  return document.documentElement.dataset.theme;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('archlens-theme', theme); } catch { /* private mode */ }
  configureMermaid();
  // Mermaid bakes the theme into the rendered SVG, so re-render on change.
  if (state.result) renderFlows(state.result.architecture.flows || []);
}

(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('archlens-theme'); } catch { /* ignore */ }
  const prefersLight = window.matchMedia?.('(prefers-color-scheme: light)').matches;
  applyTheme(saved || (prefersLight ? 'light' : 'dark'));
})();

els.themeToggle.addEventListener('click', () => {
  applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
});

/* ── Capability probe ───────────────────────────────────────────────────── */

async function loadCapabilities() {
  try {
    const health = await api('/api/health');
    const caps = [
      ['diagrams', health.diagrams, 'diagrams'],
      ['Graphviz', health.graphviz, 'graphviz'],
      ['Claude', health.anthropic_sdk && health.anthropic_key, 'claude'],
    ];
    els.capabilities.innerHTML = caps.map(([label, ok]) =>
      `<span class="cap ${ok ? 'ok' : 'miss'}" title="${ok ? 'Available' : 'Not available'}">${esc(label)}</span>`
    ).join('');

    if (!health.anthropic_sdk || !health.anthropic_key) {
      els.useLlm.checked = false;
      els.useLlm.disabled = true;
      els.useLlm.closest('.check').classList.add('disabled');
      els.llmHint.textContent = health.anthropic_sdk
        ? 'Set ANTHROPIC_API_KEY to enable — static analysis will still run'
        : 'Install the `anthropic` package to enable';
    }
  } catch {
    els.capabilities.innerHTML = '<span class="cap miss">server unreachable</span>';
  }
}

/* ── Scan lifecycle ─────────────────────────────────────────────────────── */

els.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const path = els.path.value.trim();
  if (!path) return;

  setBusy(true);
  showSection('none');
  els.progress.hidden = false;
  setProgress('Starting…', 0.01);

  try {
    const job = await api('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path,
        use_llm: els.useLlm.checked,
        dark: els.dark.checked,
        direction: els.direction.value,
      }),
    });
    state.jobId = job.id;
    try { localStorage.setItem('archlens-last-path', path); } catch { /* ignore */ }
    pollJob(job.id);
  } catch (error) {
    setBusy(false);
    els.progress.hidden = true;
    showError(error.message);
  }
});

function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      setProgress(job.message, job.progress);

      if (job.status === 'done') {
        const result = await api(`/api/jobs/${jobId}/result`);
        setBusy(false);
        els.progress.hidden = true;
        renderResult(result);
        pushHistory(job, result);
      } else if (job.status === 'error') {
        setBusy(false);
        els.progress.hidden = true;
        showError(job.error || 'The scan failed.');
      } else {
        pollJob(jobId);
      }
    } catch (error) {
      setBusy(false);
      els.progress.hidden = true;
      showError(error.message);
    }
  }, 600);
}

function setBusy(busy) {
  els.scanBtn.disabled = busy;
  els.scanBtn.querySelector('.btn-label').textContent = busy ? 'Analysing…' : 'Analyse codebase';
}

function setProgress(message, fraction) {
  els.progressMsg.textContent = message;
  els.progressBar.style.width = `${Math.round((fraction || 0) * 100)}%`;
}

function showSection(which) {
  els.empty.hidden = which !== 'empty';
  els.errorState.hidden = which !== 'error';
  els.results.hidden = which !== 'results';
}

function showError(message) {
  els.errorText.textContent = message;
  showSection('error');
}

/* ── History ────────────────────────────────────────────────────────────── */

function pushHistory(job, result) {
  state.history = [
    { id: job.id, name: result.architecture.name, root: job.root, elapsed: job.elapsed },
    ...state.history.filter((h) => h.id !== job.id),
  ].slice(0, 8);

  els.historyPanel.hidden = state.history.length === 0;
  els.historyList.innerHTML = state.history.map((item) => `
    <li><button data-job="${esc(item.id)}">
      <span class="h-name">${esc(item.name)}</span>
      <span class="h-meta">${esc(item.root)} · ${esc(item.elapsed)}s</span>
    </button></li>`).join('');

  els.historyList.querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        renderResult(await api(`/api/jobs/${button.dataset.job}/result`));
      } catch (error) {
        showError(error.message);
      }
    });
  });
}

/* ── Result rendering ───────────────────────────────────────────────────── */

function renderResult(result) {
  state.result = result;
  const { architecture: arch, evidence } = result;

  els.projectName.textContent = arch.name;
  els.projectSummary.textContent = arch.summary || '';

  const stats = [
    [num(evidence.file_count), 'Files', false],
    [num(evidence.total_loc), 'Lines', false],
    [num(arch.components.length), 'Components', true],
    [num(arch.edges.length), 'Connections', false],
    [num(evidence.routes?.length || 0), 'Routes', false],
    [`${result.duration_seconds}s`, 'Duration', false],
  ];
  els.statChips.innerHTML = stats.map(([value, label, accent]) => `
    <div class="chip ${accent ? 'accent' : ''}">
      <span class="chip-value">${esc(value)}</span>
      <span class="chip-label">${esc(label)}</span>
    </div>`).join('');

  const warnings = result.warnings || [];
  els.warnings.hidden = warnings.length === 0;
  if (warnings.length) {
    els.warnings.innerHTML =
      `<strong>${warnings.length} note${warnings.length > 1 ? 's' : ''}</strong>
       <ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>`;
  }

  renderDiagrams(result);
  renderFlows(arch.flows || []);
  renderComponents(arch);
  renderStack(arch);
  renderEvidence(evidence);

  showSection('results');
  selectTab('architecture');
}

/* ── Diagram viewer ─────────────────────────────────────────────────────── */

function renderDiagrams(result) {
  state.diagrams = (result.diagrams || []).filter((d) => d.svg || d.png);
  state.activeDiagram = 0;

  if (!state.diagrams.length) {
    els.diagramTabs.innerHTML = '';
    const failure = (result.diagrams || []).find((d) => d.error);
    els.stage.innerHTML = '';
    els.viewer.innerHTML = `<div class="viewer-empty">
        ${esc(failure?.error || 'No diagram was rendered. Check the notes above.')}
      </div>`;
    els.legend.innerHTML = '';
    return;
  }

  // The viewer element is reused across scans; restore the stage if a previous
  // render replaced it with the empty-state message.
  if (!els.viewer.contains(els.stage)) {
    els.viewer.innerHTML = '';
    els.viewer.appendChild(els.stage);
  }

  els.diagramTabs.innerHTML = state.diagrams.map((diagram, index) =>
    `<button data-index="${index}" class="${index === 0 ? 'active' : ''}">${esc(diagram.title.split('—').pop().trim())}</button>`
  ).join('');
  els.diagramTabs.querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => showDiagram(Number(button.dataset.index)));
  });

  els.legend.innerHTML = [
    ['Synchronous', '#475569', 'solid'],
    ['Async / events', '#a855f7', 'dashed'],
    ['Data read / write', '#0ea5e9', 'solid'],
  ].map(([label, color, style]) => `
    <span class="legend-item">
      <span class="legend-line" style="border-top:2px ${style} ${color}"></span>${esc(label)}
    </span>`).join('');

  showDiagram(0);
}

function showDiagram(index) {
  const diagram = state.diagrams[index];
  if (!diagram) return;
  state.activeDiagram = index;

  els.diagramTabs.querySelectorAll('button').forEach((button, i) => {
    button.classList.toggle('active', i === index);
  });

  const base = `${state.result.diagram_base}/`;
  // Prefer SVG: it stays sharp at any zoom and the PNG is only a download.
  const source = diagram.svg || diagram.png;
  els.stage.innerHTML = `<img src="${esc(base + source)}" alt="${esc(diagram.title)}">`;

  els.downloadPng.href = diagram.png ? base + diagram.png : '#';
  els.downloadPng.style.display = diagram.png ? '' : 'none';
  els.downloadPng.download = `${state.result.architecture.name}-${diagram.png || ''}`;
  els.downloadSvg.href = diagram.svg ? base + diagram.svg : '#';
  els.downloadSvg.style.display = diagram.svg ? '' : 'none';
  els.downloadSvg.download = `${state.result.architecture.name}-${diagram.svg || ''}`;

  const image = els.stage.querySelector('img');
  image.addEventListener('load', fitDiagram, { once: true });
}

/* Pan & zoom -------------------------------------------------------------- */

const view = { scale: 1, x: 0, y: 0 };

function applyTransform() {
  els.stage.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  els.zoomLevel.textContent = `${Math.round(view.scale * 100)}%`;
}

function fitDiagram() {
  const image = els.stage.querySelector('img');
  if (!image || !image.naturalWidth) return;
  const box = els.viewer.getBoundingClientRect();
  const padding = 32;
  const scale = Math.min(
    (box.width - padding) / image.naturalWidth,
    (box.height - padding) / image.naturalHeight,
    1.6,
  );
  view.scale = Math.max(scale, 0.05);
  view.x = (box.width - image.naturalWidth * view.scale) / 2;
  view.y = (box.height - image.naturalHeight * view.scale) / 2;
  applyTransform();
}

function zoomBy(factor, originX, originY) {
  const box = els.viewer.getBoundingClientRect();
  const cx = originX ?? box.width / 2;
  const cy = originY ?? box.height / 2;
  const next = Math.min(Math.max(view.scale * factor, 0.05), 8);
  // Keep the point under the cursor fixed while scaling.
  view.x = cx - (cx - view.x) * (next / view.scale);
  view.y = cy - (cy - view.y) * (next / view.scale);
  view.scale = next;
  applyTransform();
}

els.zoomIn.addEventListener('click', () => zoomBy(1.25));
els.zoomOut.addEventListener('click', () => zoomBy(0.8));
els.zoomFit.addEventListener('click', fitDiagram);

els.viewer.addEventListener('wheel', (event) => {
  event.preventDefault();
  const box = els.viewer.getBoundingClientRect();
  zoomBy(event.deltaY < 0 ? 1.12 : 0.89, event.clientX - box.left, event.clientY - box.top);
}, { passive: false });

let dragging = null;
els.viewer.addEventListener('pointerdown', (event) => {
  dragging = { x: event.clientX - view.x, y: event.clientY - view.y };
  els.viewer.setPointerCapture(event.pointerId);
  els.viewer.classList.add('dragging');
});
els.viewer.addEventListener('pointermove', (event) => {
  if (!dragging) return;
  view.x = event.clientX - dragging.x;
  view.y = event.clientY - dragging.y;
  applyTransform();
});
const endDrag = () => { dragging = null; els.viewer.classList.remove('dragging'); };
els.viewer.addEventListener('pointerup', endDrag);
els.viewer.addEventListener('pointercancel', endDrag);

/* ── Flowcharts ─────────────────────────────────────────────────────────── */

async function renderFlows(flows) {
  if (!flows.length) {
    els.flowList.innerHTML = '<p class="viewer-empty">No flowcharts were generated.</p>';
    return;
  }

  els.flowList.innerHTML = flows.map((flow, index) => `
    <article class="flow-card">
      <div class="flow-head">
        <div>
          <h3>${esc(flow.title)}</h3>
          ${flow.description ? `<p>${esc(flow.description)}</p>` : ''}
        </div>
        <span class="flow-type">${esc(flow.diagram_type)}</span>
      </div>
      <div class="flow-body" id="flow-body-${index}"><p class="viewer-empty">Rendering…</p></div>
      <details class="flow-source">
        <summary>Mermaid source</summary>
        <pre>${esc(flow.mermaid)}</pre>
      </details>
    </article>`).join('');

  await loadMermaid();
  if (!mermaid) {
    // CDN unreachable - the source is already on the page in each card's
    // <details>, so point at that rather than leaving "Rendering…" forever.
    flows.forEach((_, index) => {
      const host = document.getElementById(`flow-body-${index}`);
      if (host) {
        host.innerHTML = '<p class="viewer-empty">Diagram rendering is unavailable offline. '
          + 'The Mermaid source is below and will render in any Mermaid viewer.</p>';
      }
    });
    return;
  }

  // Render sequentially: Mermaid mutates shared state per render and racing
  // several calls produces cross-contaminated SVGs.
  for (const [index, flow] of flows.entries()) {
    const host = document.getElementById(`flow-body-${index}`);
    if (!host) continue;
    try {
      const { svg } = await mermaid.render(`mermaid-${Date.now()}-${index}`, flow.mermaid);
      host.innerHTML = svg;
    } catch (error) {
      host.outerHTML = `<div class="flow-error">Mermaid could not render this diagram:
${esc(error?.message || error)}</div>`;
    }
  }
}

/* ── Components ─────────────────────────────────────────────────────────── */

function renderComponents(arch) {
  const clusters = new Map((arch.clusters || []).map((c) => [c.id, c.name]));
  const grouped = new Map();
  for (const component of arch.components) {
    const key = component.cluster || '';
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(component);
  }

  const degree = new Map();
  for (const edge of arch.edges) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  }

  els.componentGrid.innerHTML = [...grouped.entries()].map(([clusterId, components]) => `
    <div class="cluster-group" style="grid-column:1/-1">
      <h3>${esc(clusters.get(clusterId) || 'Ungrouped')}</h3>
      <div class="component-grid">
        ${components.map((component) => `
          <div class="component-card">
            <h4>${esc(component.name)}<span class="kind-badge">${esc(component.kind)}</span></h4>
            ${component.description ? `<p>${esc(component.description)}</p>` : ''}
            <div class="component-meta">
              <span class="meta-tag">${esc(component.service)}</span>
              <span class="meta-tag">${degree.get(component.id) || 0} links</span>
              ${(component.paths || []).slice(0, 2)
                .map((p) => `<span class="meta-tag">${esc(p)}</span>`).join('')}
            </div>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

/* ── Tech stack ─────────────────────────────────────────────────────────── */

function renderStack(arch) {
  const stack = arch.tech_stack || {};
  els.stackGrid.innerHTML = Object.entries(stack).map(([group, items]) => `
    <div class="stack-card">
      <h4>${esc(group)}</h4>
      <div class="stack-tags">
        ${items.map((item) => `<span class="stack-tag">${esc(item)}</span>`).join('')}
      </div>
    </div>`).join('') || '<p class="viewer-empty">Nothing detected.</p>';

  const highlights = arch.highlights || [];
  els.highlights.innerHTML = highlights.length ? `
    <h3>What to notice</h3>
    <ul>${highlights.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>` : '';
}

/* ── Evidence ───────────────────────────────────────────────────────────── */

function table(headers, rows, total) {
  const body = rows.map((row) =>
    `<tr>${row.map((cell) => `<td class="${cell.cls || ''}">${esc(cell.value ?? cell)}</td>`).join('')}</tr>`
  ).join('');
  const more = total > rows.length
    ? `<p class="evidence-more">Showing ${rows.length} of ${num(total)}.</p>` : '';
  return `<table>
      <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${body}</tbody>
    </table>${more}`;
}

function renderEvidence(evidence) {
  const sections = [];

  if (evidence.routes?.length) {
    sections.push(`<section>
      <h3>HTTP routes <span>${num(evidence.routes.length)} found</span></h3>
      ${table(['Method', 'Path', 'Handler', 'Source'],
        evidence.routes.slice(0, 60).map((r) => [
          { value: r.method, cls: 'method' },
          { value: r.path, cls: 'mono' },
          { value: r.handler, cls: 'mono' },
          { value: `${r.file}:${r.line}`, cls: 'mono' },
        ]), evidence.routes.length)}
    </section>`);
  }

  if (evidence.iac?.length) {
    sections.push(`<section>
      <h3>Infrastructure as code <span>${num(evidence.iac.length)} resources</span></h3>
      ${table(['Kind', 'Type', 'Name', 'Maps to', 'File'],
        evidence.iac.slice(0, 60).map((r) => [
          r.kind,
          { value: r.resource_type, cls: 'mono' },
          { value: r.name, cls: 'mono' },
          { value: r.service || '—', cls: 'mono' },
          { value: r.file, cls: 'mono' },
        ]), evidence.iac.length)}
    </section>`);
  }

  if (evidence.signals?.length) {
    sections.push(`<section>
      <h3>Detected technologies <span>${num(evidence.signals.length)} signals</span></h3>
      ${table(['Technology', 'Role', 'Confidence', 'Why'],
        evidence.signals.slice(0, 60).map((s) => [
          s.label,
          s.kind,
          { value: `${Math.round(s.confidence * 100)}%`, cls: 'mono' },
          { value: (s.evidence || [])[0] || '', cls: 'mono' },
        ]), evidence.signals.length)}
    </section>`);
  }

  const runtimeDeps = (evidence.dependencies || []).filter((d) => !d.dev);
  if (runtimeDeps.length) {
    sections.push(`<section>
      <h3>Runtime dependencies <span>${num(runtimeDeps.length)} packages</span></h3>
      ${table(['Package', 'Version', 'Ecosystem', 'Declared in'],
        runtimeDeps.slice(0, 60).map((d) => [
          { value: d.name, cls: 'mono' },
          { value: d.version || '—', cls: 'mono' },
          d.ecosystem,
          { value: d.source, cls: 'mono' },
        ]), runtimeDeps.length)}
    </section>`);
  }

  if (evidence.entrypoints?.length) {
    sections.push(`<section>
      <h3>Entrypoints <span>${num(evidence.entrypoints.length)}</span></h3>
      ${table(['File'], evidence.entrypoints.slice(0, 40).map((p) => [{ value: p, cls: 'mono' }]),
        evidence.entrypoints.length)}
    </section>`);
  }

  els.evidenceBody.innerHTML = sections.join('') ||
    '<p class="viewer-empty">No structured evidence was collected.</p>';
}

/* ── Tabs ───────────────────────────────────────────────────────────────── */

function selectTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  document.querySelectorAll('.tab-panel').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === name);
  });
  if (name === 'architecture') requestAnimationFrame(fitDiagram);
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => selectTab(tab.dataset.tab));
});

/* ── Folder picker ──────────────────────────────────────────────────────── */

async function openBrowse(path = '') {
  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(path)}`);
    state.browseCurrent = data.path;
    els.browsePath.textContent = data.path || 'Pick a starting point';
    els.browseUp.disabled = !data.parent;
    els.browseUp.dataset.parent = data.parent || '';
    els.browseSelect.disabled = !data.path;

    els.browseList.innerHTML = data.entries.map((entry) => `
      <li><button data-path="${esc(entry.path)}">
        ${entry.is_repo ? '<span class="repo-dot" title="Git repository"></span>' : '<span class="spacer"></span>'}
        ${esc(entry.name)}
      </button></li>`).join('') || '<li><button disabled>No subfolders</button></li>';

    els.browseList.querySelectorAll('button[data-path]').forEach((button) => {
      button.addEventListener('click', () => openBrowse(button.dataset.path));
    });

    if (!els.browseDialog.open) els.browseDialog.showModal();
  } catch (error) {
    showError(error.message);
  }
}

els.browseBtn.addEventListener('click', () => openBrowse(els.path.value.trim()));
els.browseUp.addEventListener('click', () => openBrowse(els.browseUp.dataset.parent));
els.browseClose.addEventListener('click', () => els.browseDialog.close());
els.browseSelect.addEventListener('click', () => {
  els.path.value = state.browseCurrent;
  els.browseDialog.close();
});

/* ── Boot ───────────────────────────────────────────────────────────────── */

loadCapabilities();
try {
  const last = localStorage.getItem('archlens-last-path');
  if (last) els.path.value = last;
} catch { /* ignore */ }

window.addEventListener('resize', () => {
  if (!els.results.hidden) fitDiagram();
});

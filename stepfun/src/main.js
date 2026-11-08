import {
  MODELS,
  loadModel,
  getCurrentModel,
  embedTexts,
} from './embedder.js';
import { splitIntoChunks, readDocumentText, makeDocId } from './chunker.js';
import { seekdb, readConnConfig } from './seekdb-client.js';

const $ = (id) => document.getElementById(id);
const setStatus = (id, msg, cls = '') => {
  const el = $(id);
  el.textContent = msg;
  el.className = 'status ' + cls;
};

// ---- metrics recording ----
const metrics = JSON.parse(localStorage.getItem('seeksearch-metrics') || '[]');
function recordMetric(label, value) {
  metrics.unshift({ label, value, at: new Date().toLocaleString() });
  metrics.splice(80);
  localStorage.setItem('seeksearch-metrics', JSON.stringify(metrics));
  renderMetrics();
}
function renderMetrics() {
  $('metrics-body').innerHTML = metrics
    .map((m) => `<tr><td>${m.label}</td><td>${m.value}</td></tr>`)
    .join('');
}
renderMetrics();

$('btn-export-perf').onclick = () => {
  const model = getCurrentModel();
  const out = {
    generatedAt: new Date().toISOString(),
    model: model
      ? { repo: MODELS[model.modelKey].repo, dim: model.dim, dtype: model.dtype, device: model.device }
      : null,
    measurements: metrics,
  };
  downloadJSON(out, 'performance-results.json');
};

function downloadJSON(obj, name) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}

// ---- 1. seekdb connect ----
$('btn-connect').onclick = async () => {
  const conn = readConnConfig();
  setStatus('seekdb-status', '连接中...');
  try {
    await seekdb.ping();
    const r = await seekdb.connect(conn);
    setStatus('seekdb-status', `✓ 已连接 collection=${r.collection} 已有 ${r.count} 条向量`, 'ok');
    refreshDocList();
  } catch (e) {
    setStatus('seekdb-status', `✗ ${e.message}`, 'err');
  }
};

// ---- 2. model load ----
$('btn-load-model').onclick = async () => {
  const modelKey = $('model-select').value;
  const dtype = $('dtype-select').value;
  const devicePref = $('device-select').value;
  window.__currentDim = MODELS[modelKey].dim;
  setStatus('model-status', '加载模型中（首次需下载权重）...');
  try {
    const info = await loadModel(modelKey, dtype, devicePref);
    setStatus('model-status', `✓ 加载完成`, 'ok');
    $('device-badge').textContent = `推理后端: ${info.device.toUpperCase()} | 精度: ${info.dtype} | 加载 ${Math.round(info.loadMs)}ms`;
    $('model-info').textContent = `${info.repo} · ${info.dim} 维 · device=${info.device} · dtype=${info.dtype}`;
    recordMetric(`模型加载 ${info.repo} [${info.dtype}/${info.device}]`, `${Math.round(info.loadMs)} ms`);
  } catch (e) {
    setStatus('model-status', `✗ ${e.message}`, 'err');
  }
};

// ---- 3. ingest ----
$('btn-ingest').onclick = async () => {
  const model = getCurrentModel();
  if (!model) return setStatus('ingest-status', '✗ 请先加载模型', 'err');
  const conn = readConnConfig();

  const files = $('file-input').files;
  const paste = $('paste-area').value.trim();
  const targetSize = Number($('chunk-size').value) || 400;
  const overlap = Number($('chunk-overlap').value) || 60;
  const batchSize = Number($('batch-size').value) || 16;

  const docs = [];
  for (const f of files) {
    const text = await readDocumentText(f);
    if (text.trim()) docs.push({ name: f.name, text });
  }
  if (paste) docs.push({ name: `粘贴文本-${Date.now()}.txt`, text: paste });
  if (docs.length === 0) return setStatus('ingest-status', '✗ 请选择文件或粘贴文本', 'err');

  try {
    const t0 = performance.now();
    let totalChunks = 0;
    for (const doc of docs) {
      const chunks = splitIntoChunks(doc.text, { targetSize, overlap });
      if (chunks.length === 0) continue;
      const docId = makeDocId(doc.name, doc.text);
      const ids = chunks.map((_, i) => `${docId}#${i}`);
      setStatus('ingest-status', `向量化 ${doc.name}: 0/${chunks.length} ...`);

      const { embeddings, elapsedMs, computed, textsPerSec } = await embedTexts(chunks, {
        batchSize,
        onProgress: ({ done, total }) =>
          setStatus('ingest-status', `向量化 ${doc.name}: ${done}/${total} ...`),
      });

      await seekdb.addDocs({
        conn,
        ids,
        documents: chunks,
        embeddings,
        metadatas: chunks.map((_, i) => ({
          docId,
          docName: doc.name,
          chunkIndex: i,
          kind: 'chunk',
          importedAt: new Date().toISOString(),
        })),
      });
      totalChunks += chunks.length;
      recordMetric(
        `导入 ${doc.name}（${chunks.length} 块, ${model.dtype}/${model.device}）`,
        `${Math.round(elapsedMs)} ms · ${textsPerSec.toFixed(1)} chunks/s · 缓存命中 ${chunks.length - computed}`
      );
    }
    const secs = (performance.now() - t0) / 1000;
    setStatus(
      'ingest-status',
      `✓ 导入完成：${docs.length} 篇文档 / ${totalChunks} 个分块 / 耗时 ${secs.toFixed(1)}s`,
      'ok'
    );
    refreshDocList();
  } catch (e) {
    setStatus('ingest-status', `✗ ${e.message}`, 'err');
  }
};

async function refreshDocList() {
  try {
    const r = await seekdb.listDocs(readConnConfig());
    $('doc-list').innerHTML = r.docs
      .map(
        (d) =>
          `<div class="doc-item"><span>${d.name}（${d.chunks} 块）</span><button data-doc="${d.docId}">删除</button></div>`
      )
      .join('');
    $('doc-list')
      .querySelectorAll('button')
      .forEach((b) =>
        (b.onclick = async () => {
          await seekdb.deleteByDoc(readConnConfig(), b.dataset.doc);
          refreshDocList();
        })
      );
  } catch {
    $('doc-list').innerHTML = '';
  }
}

// ---- 4. hybrid search ----
$('btn-search').onclick = doSearch;
$('query-input').addEventListener('keydown', (e) => e.key === 'Enter' && doSearch());

async function doSearch() {
  const model = getCurrentModel();
  const q = $('query-input').value.trim();
  if (!model) return setStatus('search-status', '✗ 请先加载模型', 'err');
  if (!q) return setStatus('search-status', '✗ 请输入查询', 'err');
  const conn = readConnConfig();
  const kw = $('kw-input').value.trim();
  const nResults = 8;

  setStatus('search-status', '本地向量化查询...');
  try {
    const t0 = performance.now();
    const { embeddings } = await embedTexts([q], { batchSize: 1 });
    const embedMs = performance.now() - t0;

    const t1 = performance.now();
    const r = await seekdb.hybridSearch(conn, {
      queryEmbeddings: embeddings[0],
      whereDocument: kw ? { $contains: kw } : undefined,
      nResults,
    });
    const searchMs = performance.now() - t1;
    recordMetric(`检索 "${q.slice(0, 24)}" [${model.device}]`, `embed ${Math.round(embedMs)}ms + seekdb ${Math.round(searchMs)}ms`);

    setStatus(
      'search-status',
      `✓ 命中 ${r.ids.length} 条 · 本地向量化 ${Math.round(embedMs)}ms · seekdb 混合检索 ${Math.round(searchMs)}ms`,
      'ok'
    );
    renderResults(r, q, kw);
  } catch (e) {
    setStatus('search-status', `✗ ${e.message}`, 'err');
  }
}

function highlight(text, kw) {
  if (!kw) return escapeHtml(text);
  const parts = text.split(new RegExp(`(${kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'));
  return parts
    .map((p) => (p.toLowerCase() === kw.toLowerCase() ? `<mark>${escapeHtml(p)}</mark>` : escapeHtml(p)))
    .join('');
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderResults(r, q, kw) {
  if (!r.ids.length) {
    $('search-results').innerHTML = '<p class="hint">无结果</p>';
    return;
  }
  $('search-results').innerHTML = r.ids
    .map((id, i) => {
      const meta = r.metadatas[i] || {};
      return `<div class="result">
        <div class="result-head">#${i + 1} · rank=${(1 / (i + 1)).toFixed(3)} · ${escapeHtml(meta.docName || '')} · 块${meta.chunkIndex ?? '?'}</div>
        <div class="result-text">${highlight(r.documents[i] || '', kw || q)}</div>
      </div>`;
    })
    .join('');
}

// auto-connect on load
window.addEventListener('DOMContentLoaded', () => {
  $('btn-connect').click();
});

// Browser-local embedding via Transformers.js v3 + ONNX Runtime Web.
// - Device: WebGPU first, automatic fallback to WASM when unavailable.
// - Quantization: fp32 (baseline) / fp16 / q8 (INT8) ONNX models.
// - Batching + content-hash cache to avoid recomputing identical chunks.
import { pipeline, env } from '@huggingface/transformers';

// Always fetch model weights from the HF CDN (no local model dir in browser),
// quantized ONNX weights are loaded directly into ONNX Runtime Web.
env.allowLocalModels = false;
env.backends.onnx.wasm.numThreads = 1; // 更稳的 WASM 单线程，避免跨域 worker 限制

export const MODELS = {
  'all-MiniLM-L6-v2': {
    repo: 'Xenova/all-MiniLM-L6-v2',
    dim: 384,
    desc: 'all-MiniLM-L6-v2（384 维，英文/多语言，速度快）',
  },
  'bge-small-zh-v1.5': {
    repo: 'Xenova/bge-small-zh-v1.5',
    dim: 512,
    desc: 'bge-small-zh-v1.5（512 维，中文优化）',
  },
};

let current = null; // { extractor, modelKey, dtype, device, dim, loadMs }

async function detectWebGPU() {
  if (!('gpu' in navigator)) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return !!adapter;
  } catch {
    return false;
  }
}

/**
 * Load a feature-extraction pipeline.
 * @param {keyof typeof MODELS} modelKey
 * @param {'fp32'|'fp16'|'q8'} dtype  q8 = INT8 quantized
 * @param {'auto'|'webgpu'|'wasm'} devicePref
 * @returns resolved info { device, dtype, loadMs, dim }
 */
export async function loadModel(modelKey, dtype, devicePref) {
  const spec = MODELS[modelKey];
  if (!spec) throw new Error(`Unknown model: ${modelKey}`);

  let device = devicePref;
  if (device === 'auto' || !device) {
    device = (await detectWebGPU()) ? 'webgpu' : 'wasm';
  }
  if (device === 'webgpu' && !(await detectWebGPU())) {
    throw new Error('WebGPU 不可用（当前浏览器/环境不支持），请选择 WASM 或自动模式');
  }

  const t0 = performance.now();
  const extractor = await pipeline('feature-extraction', spec.repo, {
    dtype, // fp32 | fp16 | q8(INT8)
    device, // webgpu | wasm
  });
  const loadMs = performance.now() - t0;

  current = { extractor, modelKey, dtype, device, dim: spec.dim, loadMs };
  return { ...current, repo: spec.repo };
}

export function getCurrentModel() {
  return current ? { ...current } : null;
}

const embedCache = new Map(); // text hash -> Float32Array
function hashText(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16);
}

/**
 * Embed a list of texts in batches (mean pooling + L2 normalize).
 * @returns {number[][]} embeddings
 */
export async function embedTexts(texts, { batchSize = 16, onProgress } = {}) {
  if (!current) throw new Error('模型尚未加载');
  const out = new Array(texts.length);
  const t0 = performance.now();
  let computed = 0;

  for (let start = 0; start < texts.length; start += batchSize) {
    const slice = texts.slice(start, start + batchSize);
    const batchOut = new Array(slice.length);
    const missing = [];

    // cache hit first
    slice.forEach((t, i) => {
      const h = hashText(t);
      const cached = embedCache.get(h);
      if (cached) batchOut[i] = cached;
      else missing.push({ t, i, h });
    });

    if (missing.length > 0) {
      const tensor = await current.extractor(
        missing.map((m) => m.t),
        { pooling: 'mean', normalize: true }
      );
      const list = tensor.tolist(); // number[][]
      missing.forEach((m, k) => {
        const vec = list[k];
        embedCache.set(m.h, vec);
        batchOut[m.i] = vec;
      });
      computed += missing.length;
    }

    for (let i = 0; i < slice.length; i++) out[start + i] = batchOut[i];
    if (onProgress) {
      onProgress({
        done: Math.min(start + batchSize, texts.length),
        total: texts.length,
      });
    }
  }

  const elapsedMs = performance.now() - t0;
  return {
    embeddings: out,
    elapsedMs,
    computed,
    cacheHits: texts.length - computed,
    textsPerSec: texts.length / (elapsedMs / 1000),
  };
}

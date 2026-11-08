import { defineConfig } from 'vite';

// Browser app entry: index.html. All embedding inference runs in the browser
// via Transformers.js + ONNX Runtime Web (WebGPU backend with WASM fallback).
// seekdb access goes through the local seekdb.js proxy (/api -> 127.0.0.1:8787).
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
  build: {
    // onnxruntime-web ships large wasm/webgpu bundles; keep chunk size sane
    chunkSizeWarningLimit: 4096,
  },
});

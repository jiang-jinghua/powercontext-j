/* proxy.mjs —— 本地开发代理：静态站点 + seekdb / PowerContext 转发
 *
 * 作用：
 *  1. 托管本目录静态站点（浏览器打开 http://127.0.0.1:8787 即可使用）
 *  2. 把前端的请求转发到真实服务，避免浏览器 CORS 限制：
 *     POST /seekdb/query     -> https://d0.seekdb.ai/api/v1/query
 *     POST /powercontext/*   -> <PowerContext Server>/*（默认 http://127.0.0.1:8000）
 *
 * 启动：node server/proxy.mjs
 * 可选环境变量：PORT（默认 8787）、POWERCONTEXT_URL（默认 http://127.0.0.1:8000）
 *
 * 说明：seekdb / PowerContext 的密钥只保存在浏览器 localStorage 中，
 * 由前端随请求体携带后转发；代理本身不落盘任何凭据。
 */
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const PORT = Number(process.env.PORT || 8787);
const PC_URL = (process.env.POWERCONTEXT_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const SEEKDB_QUERY = 'https://d0.seekdb.ai/api/v1/query';

const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
};

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function forward(url, body, headers = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body,
  });
  const text = await r.text();
  return { status: r.status, text };
}

const server = http.createServer(async (req, res) => {
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
  };
  if (req.method === 'OPTIONS') {
    res.writeHead(204, cors); res.end(); return;
  }

  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    // ---- API 转发 ----
    if (req.method === 'POST' && url.pathname === '/seekdb/query') {
      const body = await readBody(req);
      const out = await forward(SEEKDB_QUERY, body);
      res.writeHead(out.status, { ...cors, 'Content-Type': 'application/json' });
      res.end(out.text); return;
    }
    if (req.method === 'POST' && url.pathname.startsWith('/powercontext/')) {
      const sub = url.pathname.replace('/powercontext', '');
      const body = await readBody(req);
      const auth = req.headers.authorization || '';
      const headers = auth ? { Authorization: auth } : {};
      const out = await forward(PC_URL + sub, body, headers);
      res.writeHead(out.status, { ...cors, 'Content-Type': 'application/json' });
      res.end(out.text); return;
    }

    // ---- 静态文件 ----
    let path = url.pathname === '/' ? '/index.html' : url.pathname;
    path = normalize(path).replace(/^(\.\.[/\\])+/, '');
    const file = join(ROOT, path);
    if (!file.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
    const data = await readFile(file);
    res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
    res.end(data);
  } catch (e) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not Found: ' + e.message);
  }
});

server.listen(PORT, () => {
  console.log(`[wengu-proxy] http://127.0.0.1:${PORT}  (PowerContext -> ${PC_URL})`);
});

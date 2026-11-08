// Local seekdb.js proxy (thin HTTP wrapper around the seekdb Node.js SDK).
//
// The seekdb JS SDK speaks the MySQL wire protocol (via mysql2), which a
// browser cannot do. All embedding vectors are computed IN THE BROWSER
// (Transformers.js + ONNX Runtime Web, WebGPU/WASM); this proxy only
// forwards already-computed vectors + queries to seekdb. No model runs here,
// no cloud API is called.
//
// Endpoints (POST /api/rpc  {action, args}):
//   connect        - open a SeekdbClient + get/create collection
//   addDocs        - collection.add({ids, documents, metadatas, embeddings})
//   deleteByDoc    - collection.delete({where: {docId}})
//   listDocs       - collection.get({where: {kind: 'chunk'}}) aggregated by doc
//   docCount       - collection.count()
//   hybridSearch   - collection.hybridSearch({query, knn:{queryEmbeddings}, rank:{rrf:{}}})
//   vectorSearch   - collection.query({queryEmbeddings})  (对照实验用)
import { createServer } from 'node:http';
import { SeekdbClient } from 'seekdb';

const PORT = 8787;
const COLLECTION_PREFIX = 'semantic_chunks';

let client = null;      // SeekdbClient instance
let collection = null;  // active collection handle
let connInfo = null;    // {host,port,user,password,database,dim}

function collectionName(dim) {
  return `${COLLECTION_PREFIX}_${dim}d`;
}

async function ensureConnected(args) {
  const { host, port, user, password, database, dim } = args;
  const same =
    client &&
    connInfo &&
    connInfo.host === host &&
    connInfo.port === port &&
    connInfo.database === database &&
    connInfo.dim === dim;
  if (!same) {
    client = new SeekdbClient({
      host,
      port: Number(port),
      user: user || 'root',
      password: password || '',
      database,
    });
    connInfo = { host, port, user, password, database, dim };
    collection = null;
  }
  if (!collection) {
    collection = await client.getOrCreateCollection({
      name: collectionName(dim),
    });
  }
  return collection;
}

const handlers = {
  async ping() {
    return { ok: true, proxy: 'seekdb.js', version: '1.0.0' };
  },

  async connect(args) {
    const col = await ensureConnected(args);
    let count = 0;
    try {
      count = await col.count();
    } catch {
      count = 0;
    }
    return { ok: true, collection: collectionName(args.dim), count };
  },

  async addDocs(args) {
    const col = await ensureConnected(args.conn);
    const { ids, documents, metadatas, embeddings } = args;
    // 写入前按 docId 删除旧块 -> 幂等导入（重复导入不产生重复数据）
    const docIds = [...new Set(metadatas.map((m) => m.docId))];
    for (const docId of docIds) {
      try {
        await col.delete({ where: { docId } });
      } catch {
        /* collection may be empty */
      }
    }
    await col.add({ ids, documents, metadatas, embeddings });
    try {
      await col.refresh_index?.();
    } catch {
      /* older seekdb versions may not support refresh */
    }
    const count = await col.count();
    return { ok: true, added: ids.length, total: count };
  },

  async deleteByDoc(args) {
    const col = await ensureConnected(args.conn);
    await col.delete({ where: { docId: args.docId } });
    const count = await col.count();
    return { ok: true, deleted: args.docId, total: count };
  },

  async listDocs(args) {
    const col = await ensureConnected(args.conn);
    const rows = await col.get({ limit: 10000, include: ['metadatas'] });
    const byDoc = new Map();
    const metas = rows.metadatas || [];
    (rows.ids || []).forEach((id, i) => {
      const m = metas[i] || {};
      if (!byDoc.has(m.docId)) {
        byDoc.set(m.docId, {
          docId: m.docId,
          name: m.docName || m.docId,
          chunks: 0,
          importedAt: m.importedAt || '',
        });
      }
      byDoc.get(m.docId).chunks++;
    });
    return { ok: true, docs: [...byDoc.values()], total: rows.ids?.length || 0 };
  },

  async docCount(args) {
    const col = await ensureConnected(args.conn);
    return { ok: true, count: await col.count() };
  },

  // 混合检索：全文 + 向量由 seekdb 完成，RRF 融合
  async hybridSearch(args) {
    const col = await ensureConnected(args.conn);
    const { queryEmbeddings, whereDocument, nResults } = args;
    const result = await col.hybridSearch({
      query: {
        ...(whereDocument ? { whereDocument } : {}),
        nResults: nResults * 2,
      },
      knn: {
        queryEmbeddings: [queryEmbeddings],
        nResults: nResults * 2,
      },
      rank: { rrf: {} },
      nResults,
    });
    return {
      ok: true,
      ids: result.ids?.[0] ?? result.ids ?? [],
      documents: result.documents?.[0] ?? result.documents ?? [],
      metadatas: result.metadatas?.[0] ?? result.metadatas ?? [],
      distances: result.distances?.[0] ?? [],
    };
  },

  // 纯向量检索（对照实验）
  async vectorSearch(args) {
    const col = await ensureConnected(args.conn);
    const result = await col.query({
      queryEmbeddings: [args.queryEmbeddings],
      nResults: args.nResults,
    });
    return {
      ok: true,
      ids: result.ids?.[0] ?? [],
      documents: result.documents?.[0] ?? [],
      metadatas: result.metadatas?.[0] ?? [],
      distances: result.distances?.[0] ?? [],
    };
  },
};

const server = createServer(async (req, res) => {
  // CORS (浏览器直连代理时需要)
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  if (req.method === 'OPTIONS') {
    res.writeHead(204).end();
    return;
  }

  if (req.method !== 'POST' || !req.url.startsWith('/api/rpc')) {
    res.writeHead(404).end(JSON.stringify({ ok: false, error: 'not found' }));
    return;
  }

  let body = '';
  req.on('data', (c) => (body += c));
  req.on('end', async () => {
    try {
      const { action, args = {} } = JSON.parse(body);
      const fn = handlers[action];
      if (!fn) throw new Error(`Unknown action: ${action}`);
      const data = await fn(args);
      res.writeHead(200).end(JSON.stringify({ ok: true, ...data }));
    } catch (err) {
      console.error('[seekdb-proxy] error:', err.message);
      res.writeHead(200).end(JSON.stringify({ ok: false, error: String(err.message || err) }));
    }
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[seekdb-proxy] listening on http://127.0.0.1:${PORT} (seekdb.js SDK)`);
});

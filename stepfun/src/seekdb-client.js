// Browser-side client for the local seekdb proxy (/api/rpc).
// All vectors passed here are computed in the browser (see embedder.js).

async function rpc(action, args = {}) {
  const res = await fetch('/api/rpc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, args }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'seekdb proxy error');
  return data;
}

export function readConnConfig() {
  const g = (id, def) => document.getElementById(id).value.trim() || def;
  return {
    host: g('seekdb-host', '127.0.0.1'),
    port: Number(g('seekdb-port', '2881')),
    user: g('seekdb-user', 'root'),
    password: document.getElementById('seekdb-password').value,
    database: g('seekdb-database', 'test'),
    dim: window.__currentDim || 384,
  };
}

export const seekdb = {
  ping: () => rpc('ping'),
  connect: (conn) => rpc('connect', conn),
  addDocs: ({ conn, ids, documents, metadatas, embeddings }) =>
    rpc('addDocs', { conn, ids, documents, metadatas, embeddings }),
  deleteByDoc: (conn, docId) => rpc('deleteByDoc', { conn, docId }),
  listDocs: (conn) => rpc('listDocs', { conn }),
  hybridSearch: (conn, { queryEmbeddings, whereDocument, nResults }) =>
    rpc('hybridSearch', { conn, queryEmbeddings, whereDocument, nResults }),
  vectorSearch: (conn, { queryEmbeddings, nResults }) =>
    rpc('vectorSearch', { conn, queryEmbeddings, nResults }),
};

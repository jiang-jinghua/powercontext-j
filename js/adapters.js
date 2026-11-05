/* adapters.js —— seekdb 与 PowerContext 真实 API 适配层 + 演示模式实现
 *
 * seekdb D0 官方 API（https://d0.seekdb.ai/SKILL.md）：
 *   POST https://d0.seekdb.ai/api/v1/query
 *   body: { instance_id, password, sql, params?, timeout_seconds?, max_rows? }
 *   SELECT 响应: { columns, column_types, rows, row_count, elapsed_ms }
 *   DML/DDL 响应: { affected_rows, last_insert_id, elapsed_ms }
 *
 * PowerContext 官方 REST（github.com/oceanbase/powercontext docs/develop/api-quickstart.md）：
 *   POST /v1/scopes            { title, summary, idempotency_key } -> { scope_id }
 *   POST /v1/sources/content   { scope_id, source_id, content, metadata } -> { source }
 *   POST /v1/memory/remember   { scope_id, kind, text, reason }
 *   POST /v1/context/prepare   { scope_id, query, max_bytes } -> { status, content }
 *
 * 浏览器直连可能受 CORS / 速率限制约束，默认经本地代理 server/proxy.mjs 转发。
 */

const Adapters = (() => {
  const SEEKDB_QUERY_URL = 'https://d0.seekdb.ai/api/v1/query';

  function cfg() { return Store.getConfig(); }
  function useReal() { return cfg().mode === 'real'; }

  function log(target, path, req, res, ok, note) {
    Store.addLog({ target, path, req, res, ok, note });
  }

  async function postJson(url, body, token) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = text; }
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${typeof data === 'string' ? data : JSON.stringify(data)}`);
    return data;
  }

  /* ================= seekdb ================= */

  // 经代理或直连执行 SQL；真实模式下调用。返回统一结构。
  async function seekdbQuery(sql) {
    const c = cfg();
    const body = {
      instance_id: c.seekdbInstanceId,
      password: c.seekdbPassword,
      sql,
      timeout_seconds: 30,
      max_rows: 200,
    };
    const url = (c.proxyUrl || '').replace(/\/$/, '') + '/seekdb/query';
    try {
      const data = await postJson(url, body);
      log('seekdb', '/api/v1/query', { sql }, data, true, '经本地代理转发');
      return data;
    } catch (e) {
      log('seekdb', '/api/v1/query', { sql }, String(e.message || e), false, '代理转发失败');
      throw e;
    }
  }

  async function seekdbInitSchema() {
    const sqls = [
      `CREATE TABLE IF NOT EXISTS knowledge_points (
        id VARCHAR(64) PRIMARY KEY,
        course VARCHAR(128) NOT NULL,
        title VARCHAR(256) NOT NULL,
        content TEXT,
        tags VARCHAR(256),
        interval_days INT DEFAULT 0,
        review_count INT DEFAULT 0,
        status VARCHAR(16) DEFAULT 'learning',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )`,
      `CREATE FULLTEXT INDEX IF NOT EXISTS ft_kp_title_content ON knowledge_points(title, content)`,
    ];
    for (const sql of sqls) await seekdbQuery(sql);
    return true;
  }

  async function seekdbUpsertKp(kp) {
    const esc = s => String(s ?? '').replace(/'/g, "''");
    const sql = `INSERT INTO knowledge_points (id, course, title, content, tags, interval_days, review_count, status)
      VALUES ('${esc(kp.id)}', '${esc(kp.course)}', '${esc(kp.title)}', '${esc(kp.content)}', '${esc(kp.tags.join(','))}', ${kp.intervalDays}, ${kp.reviewCount}, '${kp.status}')
      ON DUPLICATE KEY UPDATE course=VALUES(course), title=VALUES(title), content=VALUES(content),
        tags=VALUES(tags), interval_days=VALUES(interval_days), review_count=VALUES(review_count), status=VALUES(status)`;
    await seekdbQuery(sql);
    kp.seekdbSynced = true;
    return kp;
  }

  async function seekdbSearch(q) {
    const esc = String(q).replace(/'/g, "''");
    const sql = `SELECT id, course, title, content, tags, status,
        MATCH(title, content) AGAINST('${esc}') AS score
      FROM knowledge_points
      WHERE MATCH(title, content) AGAINST('${esc}')
      ORDER BY score DESC LIMIT 10`;
    const data = await seekdbQuery(sql);
    const cols = data.columns || [];
    const idx = n => cols.indexOf(n);
    return (data.rows || []).map(r => ({
      id: r[idx('id')], course: r[idx('course')], title: r[idx('title')],
      content: r[idx('content')], tags: (r[idx('tags')] || '').split(',').filter(Boolean),
      status: r[idx('status')], score: r[idx('score')],
    }));
  }

  /* ================= PowerContext ================= */

  function pcUrl(path) {
    const c = cfg();
    return (c.proxyUrl || '').replace(/\/$/, '') + '/powercontext' + path;
  }
  function pcToken() { return cfg().pcToken || ''; }

  async function pcRemember(kind, text, reason) {
    const c = cfg();
    const body = { scope_id: c.pcScopeId, kind, text, reason };
    try {
      const data = await postJson(pcUrl('/v1/memory/remember'), body, pcToken());
      log('PowerContext', '/v1/memory/remember', body, data, true);
      return data;
    } catch (e) {
      log('PowerContext', '/v1/memory/remember', body, String(e.message || e), false);
      throw e;
    }
  }

  async function pcCaptureSource(sourceId, content, metadata) {
    const c = cfg();
    const body = { scope_id: c.pcScopeId, source_id: sourceId, content, metadata: metadata || {} };
    try {
      const data = await postJson(pcUrl('/v1/sources/content'), body, pcToken());
      log('PowerContext', '/v1/sources/content', { ...body, content: content.slice(0, 120) + '…' }, data, true);
      return data;
    } catch (e) {
      log('PowerContext', '/v1/sources/content', { ...body, content: content.slice(0, 120) + '…' }, String(e.message || e), false);
      throw e;
    }
  }

  async function pcPrepare(query, maxBytes = 4000) {
    const c = cfg();
    const body = { scope_id: c.pcScopeId, query, max_bytes: maxBytes };
    try {
      const data = await postJson(pcUrl('/v1/context/prepare'), body, pcToken());
      log('PowerContext', '/v1/context/prepare', body, data, true);
      return data;
    } catch (e) {
      log('PowerContext', '/v1/context/prepare', body, String(e.message || e), false);
      throw e;
    }
  }

  /* ================= 演示模式（mock） ================= */

  function mockSearch(q) {
    const kw = q.trim().toLowerCase();
    const kps = Store.getKps();
    const scored = kps.map(kp => {
      const hay = (kp.title + ' ' + kp.content + ' ' + kp.course + ' ' + kp.tags.join(' ')).toLowerCase();
      let s = 0;
      if (kp.title.toLowerCase().includes(kw)) s += 3;
      if (kp.content.toLowerCase().includes(kw)) s += 2;
      if (kw.split(/\s+/).filter(w => w && hay.includes(w)).length > 0) s += 1;
      return { ...kp, score: s };
    }).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, 10);
    log('seekdb(mock)', 'MATCH...AGAINST 本地模拟', { sql: `... AGAINST('${q}') ...` },
      { matched: scored.length }, true, '演示模式：本地匹配模拟全文检索语义');
    return scored;
  }

  function mockRemember(kind, text, reason) {
    log('PowerContext(mock)', '/v1/memory/remember', { kind, text, reason },
      { status: 'recorded(mock)' }, true, '演示模式：决策已记入本地，可切换真实接入');
    return { status: 'recorded(mock)' };
  }

  function mockCaptureSource(sourceId, content, metadata) {
    log('PowerContext(mock)', '/v1/sources/content', { source_id: sourceId, content: content.slice(0, 120) + '…' },
      { status: 'captured(mock)' }, true, '演示模式：Source 证据已记入本地');
    return { status: 'captured(mock)' };
  }

  function mockPrepare(query) {
    const kps = Store.getKps().filter(kp => kp.reviewCount > 0).slice(0, 5);
    const q = query.toLowerCase();
    const related = kps.filter(k => (k.title + k.course + k.tags.join('')).toLowerCase()
      .split(/\s+/).some(w => w && q.includes(w)) || q.includes(k.title.toLowerCase().slice(0, 4)));
    const lines = related.length
      ? related.map(k => `- [决策] 「${k.title}」复习 ${k.reviewCount} 次，当前间隔 ${k.intervalDays} 天，状态 ${k.status}（来自本地演示记录）`).join('\n')
      : '- （演示模式：暂无匹配的历史复习记录。真实接入 PowerContext 后，这里返回 scope 内有界历史上下文。）';
    const out = { status: 'ready', content: lines };
    log('PowerContext(mock)', '/v1/context/prepare', { query, max_bytes: 4000 }, out, true, '演示模式');
    return out;
  }

  /* ================= 统一门面 ================= */

  function upsertKp(kp) {
    Store.upsertKp(kp);
    if (useReal()) {
      // 真实模式：写本地 + 异步同步 seekdb（失败不阻塞 UI，错误已记入日志）
      seekdbUpsertKp(kp).catch(() => {});
    }
    return Promise.resolve(kp);
  }

  return {
    useReal, cfg,
    upsertKp,
    initSchema: () => useReal() ? seekdbInitSchema() : Promise.reject(new Error('演示模式无需初始化，请先切换到真实接入模式')),
    search: q => useReal() ? seekdbSearch(q) : Promise.resolve(mockSearch(q)),
    remember: (k, t, r) => useReal() ? pcRemember(k, t, r) : Promise.resolve(mockRemember(k, t, r)),
    captureSource: (id, c, m) => useReal() ? pcCaptureSource(id, c, m) : Promise.resolve(mockCaptureSource(id, c, m)),
    prepare: q => useReal() ? pcPrepare(q) : Promise.resolve(mockPrepare(q)),
  };
})();

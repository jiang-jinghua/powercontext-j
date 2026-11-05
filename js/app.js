/* app.js —— 界面逻辑与业务编排 */
(() => {
  const $ = sel => document.querySelector(sel);
  const $$ = sel => document.querySelectorAll(sel);

  /* ---------- Tab 切换 ---------- */
  $$('.tab').forEach(t => t.addEventListener('click', () => {
    $$('.tab').forEach(x => x.classList.remove('active'));
    $$('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('#panel-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'logs') renderLogs();
    if (t.dataset.tab === 'library') renderLibrary();
    if (t.dataset.tab === 'datasource') renderCfg();
  }));

  /* ---------- 统计 ---------- */
  function renderStats() {
    const kps = Store.getKps();
    const now = Date.now();
    const due = kps.filter(k => k.dueAt <= now).length;
    const mastered = kps.filter(k => k.status === 'mastered').length;
    const reviews = kps.reduce((s, k) => s + k.reviewCount, 0);
    $('#statsBar').innerHTML = [
      ['知识点总数', kps.length], ['今日待复习', due],
      ['已掌握', mastered], ['累计复习次数', reviews],
    ].map(([n, v]) => `<div class="stat"><b>${v}</b><span>${n}</span></div>`).join('');
    $('#modeBadge').textContent = Adapters.useReal() ? '数据源：seekdb + PowerContext（真实接入）' : '数据源：内置演示';
  }

  /* ---------- 今日复习 ---------- */
  function dueKps() {
    return Store.getKps().filter(k => k.dueAt <= Date.now())
      .sort((a, b) => a.dueAt - b.dueAt);
  }

  function renderReview() {
    const list = dueKps();
    if (!list.length) {
      $('#reviewArea').innerHTML = '<div class="empty">今日复习任务已清空 🎉 去知识库添加新的知识点，或明天再来。</div>';
      renderStats();
      return;
    }
    const kp = list[0];
    $('#reviewArea').innerHTML = `
      <div class="flashcard" id="flashcard">
        <div class="chip gray">${escapeHtml(kp.course)}</div>
        <div class="front" id="fcFront">${escapeHtml(kp.title)}</div>
        <div class="back" id="fcBack" style="visibility:hidden">${escapeHtml(kp.content)}</div>
      </div>
      <div class="grade-row" id="rowShow">
        <button class="btn primary" id="btnReveal">显示答案</button>
      </div>
      <div class="grade-row" id="rowGrade" style="display:none">
        <button class="btn bad" data-g="0">忘记（明天再来）</button>
        <button class="btn warn" data-g="1">模糊（间隔不变）</button>
        <button class="btn ok" data-g="2">记住（间隔延长）</button>
      </div>
      <p class="hint" style="text-align:center">剩余待复习 ${list.length} 条 · 当前间隔 ${kp.intervalDays} 天 · 已复习 ${kp.reviewCount} 次</p>`;
    $('#btnReveal').onclick = () => {
      $('#fcBack').style.visibility = 'visible';
      $('#rowShow').style.display = 'none';
      $('#rowGrade').style.display = 'flex';
    };
    $$('#rowGrade [data-g]').forEach(b => b.onclick = () => submitGrade(kp, +b.dataset.g));
  }

  async function submitGrade(kp, g) {
    Store.grade(kp, g);
    // 真实掌握判定写入 PowerContext（Memory: decision）
    if (g === 2 && kp.status === 'mastered') {
      try {
        await Adapters.remember('decision',
          `判定知识点「${kp.title}」为已掌握（连续间隔达 15 天）`,
          `用户在复习中主动评价"记住"，课程：${kp.course}`);
      } catch (e) { /* 已记入日志，不阻塞 */ }
    }
    Store.upsertKp(kp);
    // 会话级 Source 证据：写一条复习记录（真实模式下进入 PowerContext Source journal）
    try {
      await Adapters.captureSource(
        'review-' + kp.id + '-' + Date.now(),
        `复习「${kp.course} / ${kp.title}」，评价：${['忘记', '模糊', '记住'][g]}，新间隔 ${kp.intervalDays} 天`,
        { type: 'review', kp_id: kp.id, grade: g });
    } catch (e) { /* 已记入日志 */ }
    renderReview();
  }

  /* ---------- 知识库 ---------- */
  function renderLibrary() {
    const kw = ($('#libFilter').value || '').toLowerCase();
    const kps = Store.getKps().filter(k =>
      !kw || (k.course + k.title).toLowerCase().includes(kw));
    $('#libraryList').innerHTML = kps.length ? kps.map(kp => `
      <div class="card">
        <h3>${escapeHtml(kp.title)}</h3>
        <div class="meta">
          <span class="chip">${escapeHtml(kp.course)}</span>
          ${statusChip(kp)}
          间隔 ${kp.intervalDays} 天 · 复习 ${kp.reviewCount} 次 ·
          下次复习 ${new Date(kp.dueAt).toLocaleDateString()}
          ${kp.seekdbSynced ? '<span class="chip green">已同步 seekdb</span>' : ''}
        </div>
        <div class="content">${escapeHtml(kp.content)}</div>
        <div class="actions">
          <button class="btn danger" data-del="${kp.id}">删除</button>
        </div>
      </div>`).join('') : '<div class="empty">没有匹配的知识点</div>';
    $$('#libraryList [data-del]').forEach(b => b.onclick = () => {
      const list = Store.getKps().filter(k => k.id !== b.dataset.del);
      Store.saveKps(list);
      renderLibrary(); renderStats();
    });
  }

  function statusChip(kp) {
    const map = { mastered: ['green', '已掌握'], reviewing: ['orange', '复习中'], learning: ['red', '学习中'] };
    const [cls, txt] = map[kp.status] || ['gray', kp.status];
    return `<span class="chip ${cls}">${txt}</span>`;
  }

  $('#libFilter').addEventListener('input', renderLibrary);

  /* ---------- 添加知识点 ---------- */
  $('#btnAddKnowledge').onclick = () => { $('#modalAdd').style.display = 'flex'; };
  $('#btnCancelAdd').onclick = () => { $('#modalAdd').style.display = 'none'; };
  $('#btnSaveKp').onclick = async () => {
    const course = $('#kpCourse').value.trim();
    const title = $('#kpTitle').value.trim();
    if (!course || !title) { alert('课程和标题为必填项'); return; }
    const kp = Store.makeKp({
      course, title,
      content: $('#kpContent').value.trim(),
      tags: $('#kpTags').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    });
    await Adapters.upsertKp(kp);
    $('#modalAdd').style.display = 'none';
    ['kpCourse', 'kpTitle', 'kpContent', 'kpTags'].forEach(id => $('#' + id).value = '');
    renderStats(); renderReview(); renderLibrary();
  };

  /* ---------- 智能搜索 ---------- */
  $('#btnSearch').onclick = async () => {
    const q = $('#searchInput').value.trim();
    if (!q) return;
    $('#searchResults').innerHTML = '<div class="empty">检索中…</div>';
    try {
      const hits = await Adapters.search(q);
      $('#searchResults').innerHTML = hits.length ? hits.map(h => `
        <div class="card">
          <h3>${escapeHtml(h.title)}</h3>
          <div class="meta"><span class="chip">${escapeHtml(h.course)}</span>
            相关性得分 ${h.score}</div>
          <div class="content">${escapeHtml(h.content)}</div>
        </div>`).join('') : '<div class="empty">未检索到相关知识点</div>';
    } catch (e) {
      $('#searchResults').innerHTML = `<div class="empty">检索失败：${escapeHtml(String(e.message || e))}<br>请检查数据源配置与本地代理是否启动。</div>`;
    }
  };

  /* ---------- 上下文召回 ---------- */
  $('#btnCtx').onclick = async () => {
    const q = $('#ctxQuery').value.trim();
    if (!q) return;
    $('#ctxResult').textContent = '召回中…';
    try {
      const data = await Adapters.prepare(q);
      $('#ctxResult').textContent = `status: ${data.status}\n\n${data.content || '（无相关历史上下文）'}`;
    } catch (e) {
      $('#ctxResult').textContent = '召回失败：' + (e.message || e) +
        '\n请确认已切换真实接入模式、本地代理与 PowerContext Server 已启动。';
    }
  };

  /* ---------- 数据源配置 ---------- */
  function renderCfg() {
    const c = Store.getConfig();
    $('#dsMock').checked = c.mode === 'mock';
    $('#dsReal').checked = c.mode === 'real';
    $('#realCfg').style.display = c.mode === 'real' ? 'block' : 'none';
    $('#cfgProxy').value = c.proxyUrl;
    $('#cfgSeekdbId').value = c.seekdbInstanceId;
    $('#cfgSeekdbPwd').value = c.seekdbPassword;
    $('#cfgPcUrl').value = c.pcUrl;
    $('#cfgPcScope').value = c.pcScopeId;
    $('#cfgPcToken').value = c.pcToken;
  }
  $$('#panel-datasource input[name=dsMode]').forEach(r => r.addEventListener('change', () => {
    $('#realCfg').style.display = $('#dsReal').checked ? 'block' : 'none';
  }));
  $('#btnSaveCfg').onclick = () => {
    const c = Store.getConfig();
    c.mode = $('#dsReal').checked ? 'real' : 'mock';
    c.proxyUrl = $('#cfgProxy').value.trim();
    c.seekdbInstanceId = $('#cfgSeekdbId').value.trim();
    c.seekdbPassword = $('#cfgSeekdbPwd').value.trim();
    c.pcUrl = $('#cfgPcUrl').value.trim();
    c.pcScopeId = $('#cfgPcScope').value.trim();
    c.pcToken = $('#cfgPcToken').value.trim();
    Store.saveConfig(c);
    renderStats();
    alert('已保存。' + (c.mode === 'real'
      ? '真实接入模式已开启，请确保本地代理已启动（node server/proxy.mjs）。'
      : '已切换回内置演示模式。'));
  };
  $('#btnInitDb').onclick = async () => {
    try {
      await Adapters.initSchema();
      alert('seekdb 表结构初始化成功（knowledge_points + 全文索引）。');
    } catch (e) {
      alert('初始化失败：' + (e.message || e) + '\n请确认真实接入模式、代理与 seekdb 凭据已填写。');
    }
  };

  /* ---------- 调用日志 ---------- */
  function renderLogs() {
    const logs = Store.getLogs();
    $('#logList').innerHTML = logs.length ? logs.map(l => `
      <div class="log-item">
        <div class="head">
          <span><b>${escapeHtml(l.target)}</b> <code>${escapeHtml(l.path)}</code>
          ${l.note ? `<span class="chip gray">${escapeHtml(l.note)}</span>` : ''}</span>
          <span class="${l.ok ? 'status-ok' : 'status-fail'}">${l.ok ? '✓ 成功' : '✗ 失败'} · ${l.ts}</span>
        </div>
        <pre>请求 → ${escapeHtml(JSON.stringify(l.req, null, 2))}</pre>
        <pre>响应 ← ${escapeHtml(typeof l.res === 'string' ? l.res : JSON.stringify(l.res, null, 2))}</pre>
      </div>`).join('') : '<div class="empty">暂无调用记录。触发一次搜索/复习/召回后这里会出现完整的请求与响应。</div>';
  }
  $('#btnExportLog').onclick = () => {
    const blob = new Blob([JSON.stringify(Store.getLogs(), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `wengu-api-logs-${Date.now()}.json`;
    a.click();
  };
  $('#btnClearLog').onclick = () => { Store.clearLogs(); renderLogs(); };

  /* ---------- 工具 ---------- */
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* ---------- 启动 ---------- */
  renderStats();
  renderReview();
})();

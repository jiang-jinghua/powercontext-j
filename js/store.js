/* store.js —— 本地数据层 + API 调用日志 */
const Store = (() => {
  const KP_KEY = 'wengu.knowledge_points.v1';
  const LOG_KEY = 'wengu.api_logs.v1';
  const CFG_KEY = 'wengu.config.v1';

  function read(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch { return fallback; }
  }
  function write(key, val) { localStorage.setItem(key, JSON.stringify(val)); }

  // ---------- 知识点 ----------
  function getKps() { return read(KP_KEY, seedIfEmpty()); }
  function saveKps(list) { write(KP_KEY, list); }
  function upsertKp(kp) {
    const list = getKps();
    const i = list.findIndex(k => k.id === kp.id);
    if (i >= 0) list[i] = kp; else list.unshift(kp);
    saveKps(list);
    return kp;
  }

  const SAMPLE = [
    { course: '计算机网络', title: 'TCP 拥塞控制的四个阶段',
      content: '慢启动：cwnd 指数增长直到 ssthresh；拥塞避免：线性加 1；快重传：收到 3 个重复 ACK 立即重传；快恢复：cwnd 减半后进入拥塞避免。',
      tags: '网络,TCP' },
    { course: '计算机网络', title: 'HTTP/1.1 与 HTTP/2 的关键差异',
      content: 'HTTP/2 引入二进制分帧、多路复用（同一 TCP 连接并行多个流）、头部压缩 HPACK、服务器推送，解决 HTTP/1.1 队头阻塞问题。',
      tags: '网络,HTTP' },
    { course: '高等数学', title: '泰勒公式及其应用',
      content: 'f(x)=f(x0)+f\'(x0)(x-x0)+f\'\'(x0)/2!(x-x0)^2+...+Rn(x)。用于求极限、判断极值、估算误差。佩亚诺余项与拉格朗日余项的区别。',
      tags: '数学,级数' },
    { course: '数据结构', title: '快排的时间复杂度与优化',
      content: '平均 O(nlogn)，最坏 O(n^2)（已序数组+首元素基准）。优化：随机基准、三数取中、小区间插排、三路划分处理重复元素。',
      tags: '算法,排序' },
    { course: '机器学习', title: '过拟合与正则化',
      content: '过拟合：训练误差低、泛化误差高。应对：L1/L2 正则、Dropout、早停、数据增强、交叉验证选模型。',
      tags: 'ML,基础' },
    { course: '操作系统', title: '进程与线程的区别',
      content: '进程是资源分配的基本单位（独立地址空间），线程是 CPU 调度的基本单位（共享进程地址空间）。上下文切换成本：进程 > 线程。',
      tags: 'OS,并发' },
    { course: '数据库', title: 'B+ 树为什么适合做索引',
      content: '矮胖树降低磁盘 IO 次数；叶子节点链表便于范围查询；非叶子节点只存键不存数据，单页可容纳更多键。',
      tags: 'DB,索引' },
    { course: '机器学习', title: '偏差与方差',
      content: '偏差：模型预测期望与真实值的距离（欠拟合）；方差：预测随训练集扰动的波动（过拟合）。目标：偏差-方差折中。',
      tags: 'ML,基础' },
  ];

  function seedIfEmpty() {
    const existing = read(KP_KEY, null);
    if (existing) return existing;
    const now = Date.now();
    const list = SAMPLE.map((s, i) => makeKp({
      course: s.course, title: s.title, content: s.content,
      tags: s.tags.split(','),
      // 让演示数据里有一半今天到期
      dueAt: i < 4 ? now - 86400e3 : now + (i * 3) * 86400e3,
      seeded: true,
    }));
    write(KP_KEY, list);
    return list;
  }

  function makeKp({ course, title, content, tags = [], dueAt = Date.now(), seeded = false }) {
    return {
      id: 'kp_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
      course, title, content, tags,
      ease: 2.5, intervalDays: 0, dueAt,
      reviewCount: 0, status: 'learning',   // learning | reviewing | mastered
      createdAt: Date.now(),
      seekdbSynced: false,                  // 是否已真实写入 seekdb
    };
  }

  // SM-2 lite：grade 0=忘记 1=模糊 2=记住
  const STEPS = [1, 2, 4, 7, 15, 30];
  function grade(kp, g) {
    kp.reviewCount++;
    if (g === 0) {
      kp.intervalDays = 1;
      kp.ease = Math.max(1.3, kp.ease - 0.2);
      kp.status = 'learning';
    } else if (g === 1) {
      kp.ease = Math.max(1.3, kp.ease - 0.1);
      kp.status = 'reviewing';
    } else {
      const idx = STEPS.indexOf(kp.intervalDays);
      kp.intervalDays = STEPS[Math.min(STEPS.length - 1, idx + 1)];
      kp.ease = Math.min(3.0, kp.ease + 0.1);
      if (kp.intervalDays >= 15) kp.status = 'mastered';
      else kp.status = 'reviewing';
    }
    kp.dueAt = Date.now() + kp.intervalDays * 86400e3;
    return kp;
  }

  // ---------- 调用日志 ----------
  function getLogs() { return read(LOG_KEY, []); }
  function addLog(entry) {
    const logs = getLogs();
    logs.unshift({
      ts: new Date().toISOString(),
      ok: entry.ok, target: entry.target, method: entry.method || 'POST',
      path: entry.path, req: entry.req, res: entry.res, note: entry.note,
    });
    write(LOG_KEY, logs.slice(0, 300));
  }
  function clearLogs() { write(LOG_KEY, []); }

  // ---------- 配置 ----------
  function getConfig() {
    return read(CFG_KEY, {
      mode: 'mock', proxyUrl: 'http://127.0.0.1:8787',
      seekdbInstanceId: '', seekdbPassword: '',
      pcUrl: 'http://127.0.0.1:8000', pcScopeId: '', pcToken: '',
    });
  }
  function saveConfig(cfg) { write(CFG_KEY, cfg); }

  return { getKps, saveKps, upsertKp, makeKp, grade, getLogs, addLog, clearLogs, getConfig, saveConfig };
})();

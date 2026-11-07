# -*- coding: utf-8 -*-
"""老王菜摊 · 结算内核（Dify 代码节点与本地测试同源实现）

本文件是整个应用的"数值真实来源（single source of truth）"：
- Dify 代码节点里的代码由 build/build_dsl.py 从本文件注入生成，保证不漂移；
- tests/test_settle.py 与 tests/simulate.py 直接 import 本文件做单元测试与剧本回放。

角色设定：老王，早市菜摊摊主。玩家目标：6 轮内把黄瓜从 12.0 元/斤砍到 8.0 元/斤以下。
设计原则：LLM 只负责"演"（台词、招式归类、情报识别），所有数值结算在代码里确定性完成，
保证同一局输入可以得到完全可复现的结果。
"""
import json
import re

START_PRICE = 12.0   # 开局标价（元/斤）
FLOOR_PRICE = 7.2    # 老王底价（玩家不可见，套出情报后才可能逼近）
TARGET_PRICE = 8.0   # 玩家获胜目标
MAX_ROUND = 6        # 最大轮数
GENTLE_PRICE = 8.5   # 收摊前"友情价"上限
BASE_RATIO = 0.35    # 每轮基础让价 = 剩余空间 × BASE_RATIO × 招式系数 × 衰减
INTEL_BONUS = 0.15   # 套出进货价后每轮额外让价
SOFTEN_PRICE = 0.3   # 情绪高涨时的"抹零"让价

TACTIC_RULES = {
    "对比邻摊": {"coef": 1.30, "mood": -6},
    "挑刺":     {"coef": 1.10, "mood": -4},
    "走人威胁": {"coef": 1.40, "mood": -12},
    "套近乎":   {"coef": 0.90, "mood": +8},
    "卖惨":     {"coef": 0.80, "mood": +2},
    "硬砍":     {"coef": 0.50, "mood": -8},
    "试探行情": {"coef": 0.55, "mood": 0},
    "认错缓和": {"coef": 0.70, "mood": +6},
}


def _fresh_state() -> dict:
    return {
        "round": 0,
        "price": START_PRICE,
        "mood": 50,
        "tactics": {},
        "intel": 0,
        "status": "open",
        "history": [],
    }


def _load_state(raw: str) -> dict:
    try:
        st = json.loads(raw) if raw else {}
        if not isinstance(st, dict):
            st = {}
    except Exception:
        st = {}
    fresh = _fresh_state()
    for k, v in fresh.items():
        st.setdefault(k, v)
    if not isinstance(st["tactics"], dict):
        st["tactics"] = {}
    if not isinstance(st["history"], list):
        st["history"] = []
    return st


def _parse_llm(text: str) -> dict:
    """从 LLM 输出中稳健地取回 JSON；解析失败时降级为原文本台词。"""
    out = {"reply": "", "tactic": "", "intel_hit": 0, "note": ""}
    if text:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    for k in out:
                        if data.get(k) not in (None, ""):
                            out[k] = data[k]
            except Exception:
                pass
    if not str(out["reply"]).strip():
        fallback = str(text or "").strip()
        out["reply"] = fallback[:120] if fallback else "老王抬了抬眼皮：买不买？"
    try:
        out["intel_hit"] = 1 if int(out["intel_hit"] or 0) == 1 else 0
    except Exception:
        out["intel_hit"] = 0
    return out


def _match_tactic(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return "试探行情"
    if raw in TACTIC_RULES:
        return raw
    for k in TACTIC_RULES:
        if k and (k in raw or raw in k):
            return k
    return "试探行情"


def _band(mood: int) -> str:
    n = max(0, min(10, int(round(mood / 10.0))))
    return "█" * n + "░" * (10 - n)


def _panel(st: dict, tactic: str, decay: float, note: str) -> str:
    used = "、".join("%s×%d" % (k, v) for k, v in st["tactics"].items() if v) or "—"
    intel_txt = "已套出进货价（每轮多让 %.2f 元）" % INTEL_BONUS if st["intel"] else "无"
    lines = [
        "💰 **%.1f 元/斤** ｜ 目标 ≤ %.1f ｜ 第 %d/%d 轮" % (st["price"], TARGET_PRICE, st["round"], MAX_ROUND),
        "😤 老王情绪 %s %d/100" % (_band(st["mood"]), st["mood"]),
        "🗣 招式：%s" % used,
    ]
    if st["status"] == "open" and decay < 1.0:
        lines.append("🔁 「%s」是第 %d 次用了，这招效果只剩 ×%.2f" % (tactic, st["tactics"].get(tactic, 1), decay))
    lines.append("🕵 情报：%s" % intel_txt)
    if note:
        lines.append("🥚 %s" % note)
    return "\n".join(lines)


def main(llm_text: str, state_json: str) -> dict:
    st = _load_state(state_json)
    ai = _parse_llm(llm_text)

    # 上一局已结束 -> 自动开新局（可重玩）
    if st["status"] != "open":
        st = _fresh_state()
        ai["reply"] = "老王把秤重新摆正，拍了拍黄瓜上的水珠：「行，再来一局。这回可得讲理啊。」"

    tactic = _match_tactic(ai["tactic"])
    rule = TACTIC_RULES[tactic]

    count_before = int(st["tactics"].get(tactic, 0))
    decay = 1.0 if count_before < 1 else max(0.25, 1.0 - 0.4 * count_before)

    old_price = float(st["price"])
    gap = max(0.0, old_price - FLOOR_PRICE)
    drop = gap * BASE_RATIO * rule["coef"] * decay
    if st["intel"] >= 1:
        drop += INTEL_BONUS * decay
    if st["mood"] < 35:
        drop *= 0.6  # 惹毛了就不松口
    new_price = round(max(FLOOR_PRICE, old_price - drop), 1)

    # 首次套出情报：一次性冲击让价 + 标记
    intel_hit = 1 if int(ai["intel_hit"]) == 1 else 0
    if intel_hit and st["intel"] == 0:
        st["intel"] = 1
        new_price = round(max(FLOOR_PRICE, new_price - 0.2), 1)

    mood = st["mood"] + rule["mood"] + (8 if intel_hit and st["mood"] < 80 else 0)
    mood = max(0, min(100, mood))

    # 高情绪彩蛋：第 3 轮起心情好就"抹零"
    if mood >= 85 and st["round"] + 1 >= 3:
        new_price = round(max(FLOOR_PRICE, new_price - SOFTEN_PRICE), 1)

    st["round"] += 1
    st["price"] = new_price
    st["mood"] = mood
    st["tactics"][tactic] = count_before + 1
    st["history"].append([st["round"], tactic, new_price])

    # 结局判定
    if mood < 20:
        status = "broke"
    elif new_price <= TARGET_PRICE + 1e-9:
        status = "deal"
    elif st["round"] >= MAX_ROUND:
        if new_price <= GENTLE_PRICE:
            status = "deal"
            new_price = round(min(new_price, GENTLE_PRICE), 1)
            st["price"] = new_price
        else:
            status = "timeout"
    else:
        status = "open"
    st["status"] = status

    used_txt = "、".join("%s×%d" % (k, v) for k, v in st["tactics"].items() if v) or "—"

    if status == "deal":
        saved = round(START_PRICE - new_price, 1)
        pct = int(round((START_PRICE - new_price) / START_PRICE * 100))
        medals = []
        if st["intel"]:
            medals.append("🏅 情报王（套出了进货价）")
        if st["round"] <= 3:
            medals.append("⚡ 一刀见血（%d 轮成交）" % st["round"])
        if new_price > TARGET_PRICE:
            medals.append("🕊 收摊友情价（离目标还差 %.1f 元，算老王认你这个人）" % (new_price - TARGET_PRICE))
        if not medals:
            medals.append("🥬 持家有道")
        reply = (
            "%s\n\n> 📢 价签：%.1f → **%.1f 元/斤**（砍下 %.1f 元，-%d%%）\n\n"
            "🎉 **成交！** 老王把两斤黄瓜往袋里一塞，又抓了把小葱扔进去：「拿走拿走，别说是我给的价格。」\n\n"
            "🏆 成就：%s ｜ 用了 %d 轮 ｜ 招式：%s"
            % (ai["reply"], START_PRICE, new_price, saved, pct, "；".join(medals), st["round"], used_txt)
        )
    elif status == "broke":
        reply = (
            "%s\n\n> 📢 老王把塑料布一掀：「不卖了不卖了，你上别家！」\n\n"
            "💥 **谈崩了**：情绪跌到 %d/100。成就：🥊 嘴强王者（成功把摊主聊急了）。"
            % (ai["reply"], mood)
        )
    elif status == "timeout":
        reply = (
            "%s\n\n> ⏰ 收摊铃响。\n\n"
            "⌛ **没谈成**：最终 %.1f 元/斤，还差 %.1f 元。老王抖了抖烟灰：「明儿早点来，好的给你留着。」"
            % (ai["reply"], new_price, new_price - TARGET_PRICE)
        )
    else:
        delta = round(new_price - old_price, 1)
        if delta <= -0.05:
            broadcast = "价签：%.1f → **%.1f** 元/斤（让了 %.1f）" % (old_price, new_price, -delta)
        else:
            broadcast = "价签没动，还是 %.1f 元/斤" % new_price
        reply = "%s\n\n> 📢 %s" % (ai["reply"], broadcast)

    panel = _panel(st, tactic, decay, ai.get("note", ""))
    return {
        "reply": reply,
        "panel": panel,
        "state_json": json.dumps(st, ensure_ascii=False),
        "status": status,
    }

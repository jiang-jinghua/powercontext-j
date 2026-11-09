# -*- coding: utf-8 -*-
"""老王菜摊 · 结算内核（服务端唯一真实来源）

设计原则：玩家的"招式"由接口传参确定，所有数值（价格、情绪、轮次、结局）
都在本文件里确定性结算，不依赖任何模型或随机数。
因此同一串输入必然得到同一串输出，verify.py 可以用同一份代码算出期望值再和
服务端返回逐字段比对 —— 这正是可复现性的来源。

玩法：6 轮内把黄瓜从 12.0 元/斤砍到 8.0 元/斤及以下。
"""

START_PRICE = 12.0   # 开局标价（元/斤）
FLOOR_PRICE = 7.2    # 老王底价（玩家不可见）
TARGET_PRICE = 8.0   # 玩家获胜目标
MAX_ROUND = 6        # 最大轮数
GENTLE_PRICE = 8.5   # 收摊前的"友情价"上限
BASE_RATIO = 0.35    # 每轮基础让价 = 剩余空间 × BASE_RATIO × 招式系数 × 衰减
INTEL_BONUS = 0.15   # 套出进货价后每轮额外让价
SOFTEN_PRICE = 0.3   # 情绪高涨时的"抹零"

# 招式表：coef 让价系数，mood 情绪增量，line 摊主台词模板
TACTICS = {
    "对比邻摊": {"coef": 1.30, "mood": -6, "line": "老王斜了一眼东头：「他家那是泡过水的，你也信？」"},
    "挑刺":     {"coef": 1.10, "mood": -4, "line": "老王捏起一根黄瓜：「你挑，随便挑，挑得出毛病算我的。」"},
    "走人威胁": {"coef": 1.40, "mood": -12, "line": "老王把秤一收：「行行行，你要走我也不拦，早市就这一家？」"},
    "套近乎":   {"coef": 0.90, "mood": 8, "line": "老王笑了：「你这孩子嘴甜，天天来我都认得你了。」"},
    "卖惨":     {"coef": 0.80, "mood": 2, "line": "老王叹气：「都不容易，我这三轮车也是刚还完货款。」"},
    "硬砍":     {"coef": 0.50, "mood": -8, "line": "老王眉毛一竖：「你这不叫砍价，这叫抢。」"},
    "试探行情": {"coef": 0.55, "mood": 0, "line": "老王压低声音：「行情我说了算？我给你透个底。」"},
    "认错缓和": {"coef": 0.70, "mood": 6, "line": "老王摆摆手：「行了行了，会说话就好办。」"},
}

CHOICES = list(TACTICS.keys())

# 口语别名 -> 标准招式（接口容错，玩家不用背招式名）
ALIASES = {
    "对比邻摊": ("邻摊", "隔壁", "人家卖", "别人家"),
    "挑刺": ("挑刺", "不新鲜", "太老了", "有斑点"),
    "走人威胁": ("走人", "不买了", "换一家", "走了"),
    "套近乎": ("套近乎", "常来", "老街坊", "夸"),
    "卖惨": ("卖惨", "没钱", "学生", "穷"),
    "硬砍": ("硬砍", "五块", "三块", "便宜点"),
    "试探行情": ("行情", "进货", "成本", "批发"),
    "认错缓和": ("认错", "缓和", "对不起", "刚才"),
}


def new_state() -> dict:
    """一局新游戏的初始状态。"""
    return {
        "round": 0,
        "price": START_PRICE,
        "mood": 50,
        "intel": 0,
        "tactics": {},
        "status": "open",
    }


def parse_action(text) -> str:
    """把玩家输入归一到 8 个标准招式之一；无法识别返回空串。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw in TACTICS:
        return raw
    for name, words in ALIASES.items():
        for w in words:
            if w and w in raw:
                return name
    return ""


def band(mood: int) -> str:
    n = max(0, min(10, int(round(mood / 10.0))))
    return "█" * n + "░" * (10 - n)


def _closing(st: dict, status: str) -> str:
    if status == "deal":
        saved = round(START_PRICE - st["price"], 1)
        pct = int(round((START_PRICE - st["price"]) / START_PRICE * 100))
        return (
            "\n\n🎉 **成交！%.1f 元/斤**（砍下 %.1f 元，-%d%%）。"
            "老王把黄瓜往袋里一塞，又抓了把小葱扔进去：「拿走拿走，别说是我给的价。」"
            " ｜ 用了 %d 轮" % (st["price"], saved, pct, st["round"])
        )
    if status == "broke":
        return (
            "\n\n💥 **谈崩了**：情绪跌到 %d/100。老王把塑料布一掀：「不卖了不卖了，你上别家！」" % st["mood"]
        )
    if status == "timeout":
        return (
            "\n\n⏰ **收摊了**：%d 轮用完，最后停在 %.1f 元/斤，没到 8.0 元的目标。"
            "老王收摊前撂下一句：「明儿早点来。」" % (st["round"], st["price"])
        )
    return ""


def settle(st: dict, tactic: str, intel_hint: str = "") -> dict:
    """推进一轮，返回新状态 + 台词。st 会被就地更新。
    intel_hint 由服务端从 seekdb 的行情表读出后传入（可为空）。
    """
    rule = TACTICS[tactic]
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

    # 首次试探行情：掏出进货价，一次性冲击让价
    first_intel = 0
    if tactic == "试探行情" and not st["intel"]:
        st["intel"] = 1
        first_intel = 1
        new_price = round(max(FLOOR_PRICE, new_price - 0.2), 1)

    mood = st["mood"] + rule["mood"] + (8 if first_intel and st["mood"] < 80 else 0)
    mood = max(0, min(100, mood))

    # 高情绪彩蛋：第 3 轮起心情够好就抹零
    if mood >= 85 and st["round"] + 1 >= 3:
        new_price = round(max(FLOOR_PRICE, new_price - SOFTEN_PRICE), 1)

    st["round"] += 1
    st["price"] = new_price
    st["mood"] = mood
    st["tactics"][tactic] = count_before + 1

    if mood < 20:
        st["status"] = "broke"
    elif new_price <= TARGET_PRICE + 1e-9:
        st["status"] = "deal"
    elif st["round"] >= MAX_ROUND:
        if new_price <= GENTLE_PRICE:
            st["status"] = "deal"
            st["price"] = round(min(new_price, GENTLE_PRICE), 1)
        else:
            st["status"] = "timeout"
    else:
        st["status"] = "open"

    lines = [rule["line"]]
    if first_intel:
        lines.append("他往筐后一指：「%s」" % (intel_hint or "这批黄瓜进价七块二，我卖你十二，你说我黑不黑？"))
    if decay < 1.0 and st["status"] == "open":
        lines.append("（「%s」你已经用了 %d 次，这回他没怎么松口。）" % (tactic, count_before + 1))
    lines.append(
        "💰 **%.1f 元/斤**｜目标 ≤ %.1f｜第 %d/%d 轮｜😤 老王情绪 %s %d/100"
        % (st["price"], TARGET_PRICE, st["round"], MAX_ROUND, band(st["mood"]), st["mood"])
    )
    if st["intel"]:
        lines.append("🕵 已套出进货价：每轮多让 %.2f 元" % INTEL_BONUS)
    lines.append(_closing(st, st["status"]).strip())
    return {"reply": "\n\n".join(x for x in lines if x), "state": st}

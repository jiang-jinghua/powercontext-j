# -*- coding: utf-8 -*-
"""老王菜摊 · 结算内核（唯一真实来源）

设计原则：招式由接口传参确定，所有数值（价格、情绪、轮次、结局）都在本文件里
确定性结算，不用模型、不用随机数。同一串输入必然得到同一串输出。

一局的规则（与 README「规则表」逐字对应；rules_check.py 用 rules_ref.py 独立复算）：

| 编号 | 规则 | 取值 |
|------|------|------|
| R1 | 开局标价 | 12.0 元/斤，情绪 50，轮次 0，未解锁情报 |
| R2 | 老王底价（任何情况不低于） | 7.2 元/斤 |
| R3 | 成交条件（唯一） | 本轮结算后价格 ≤ 8.0 → deal |
| R4 | 轮数上限 | 6 轮；第 6 轮结束必为终局，不会出现第 7 轮 |
| R5 | 每轮基础让价 | (本轮开始时的价格 − 7.2) × 0.35 × 招式系数 × 衰减 |
| R6 | 同一招式重复使用 | 第 n 次（n≥2）的衰减 = max(0.25, 1 − 0.4×(n−1))，即 ×0.6、×0.25、×0.25… |
| R7 | 本轮开始时情绪 < 35 | 本轮让价再 ×0.6（在 R5 之后、R9/R10 之前） |
| R8 | 本轮结算后情绪 ≥ 85 且轮次 ≥ 3 | 四舍五入后，再额外减 0.3 元（仅该轮） |
| R9 | 首次使用「试探行情」 | 本轮一次性多让 0.2 元，并置 intel=1（情绪不变） |
| R10 | 已解锁情报（intel=1）的后续轮次 | 每轮额外让 0.15 × 本轮衰减（解锁那一轮不加，见 R9） |
| R11 | 本轮结算后情绪 ≤ 20 | 掀摊 broke，优先级高于 R3 |
| R12 | 第 6 轮结束仍 > 8.0 | 收摊没谈成 timeout，不算成交 |
| R13 | 已终局的局再出招 | 作废旧局、开新局（restarted=true），新局第 1 轮 |

结算顺序（每轮固定，不存在其他分支）：
1. 取 decay（R6）→ 2. drop = gap×0.35×coef×decay（R5）→ 3. mood<35 则 drop×0.6（R7）
→ 4. 若 intel=1 则 drop += 0.15×decay（R10）→ 5. 若是首次「试探行情」则 drop += 0.2 且 intel=1（R9）
→ 6. 价格 = 四舍五入到 0.1 的 max(7.2, 旧价 − drop)（R2）
→ 7. 情绪 = clamp(旧情绪 + 招式情绪增减, 0, 100)（没有其他加减）
→ 8. 若新情绪 ≥ 85 且本轮 ≥ 3，价格再减 0.3 并四舍五入（R8）
→ 9. 判结局：情绪 ≤ 20 掀摊（R11）> 价格 ≤ 8.0 成交（R3）> 第 6 轮超时（R12）> 继续。

「四舍五入」一律指 half-up（0.05 进位），实现见 round1()。
"""
from decimal import Decimal, ROUND_HALF_UP

START_PRICE = 12.0     # R1 开局标价（元/斤）
START_MOOD = 50        # R1 开局情绪
FLOOR_PRICE = 7.2      # R2 老王底价（玩家不可见）
TARGET_PRICE = 8.0     # R3 成交线
MAX_ROUND = 6          # R4 轮数上限
BASE_RATIO = 0.35      # R5 每轮基础让价比例
DECAY_STEP = 0.4       # R6 每重复一次多衰减 0.4
DECAY_FLOOR = 0.25     # R6 衰减下限
LOW_MOOD_LINE = 35     # R7 低于此情绪让价打折
LOW_MOOD_RATIO = 0.6   # R7 折扣
SOFTEN_MOOD = 85       # R8 抹零所需情绪
SOFTEN_PRICE = 0.3     # R8 抹零金额
INTEL_ONE_OFF = 0.2    # R9 首次套出情报的一次性让价
INTEL_BONUS = 0.15     # R10 解锁后每轮额外让价
BROKE_MOOD = 20        # R11 掀摊阈值（≤ 即掀摊）

# 招式表：coef 让价系数（R5），mood 情绪增量，line 摊主台词
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


def round1(x: float) -> float:
    """四舍五入到 1 位小数（half-up，与文档声称的一致，不用 Python 的银行家舍入）。"""
    d = Decimal(repr(round(float(x), 6)))
    return float(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def new_state() -> dict:
    """一局新游戏的初始状态（R1）。"""
    return {
        "round": 0,
        "price": START_PRICE,
        "mood": START_MOOD,
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


def decay_of(count_before: int) -> float:
    """R6：第 count_before+1 次使用该招式时的衰减系数。"""
    if count_before < 1:
        return 1.0
    return max(DECAY_FLOOR, 1.0 - DECAY_STEP * count_before)


def band(mood: int) -> str:
    n = max(0, min(10, int(round(mood / 10.0))))
    return "█" * n + "░" * (10 - n)


def _closing(st: dict, status: str) -> str:
    """终局文案。只有 deal / broke / timeout 三种结局才有结尾，未结束（open）不加。"""
    if status == "open":
        return ""
    if status == "deal":
        saved = round1(START_PRICE - st["price"])
        pct = int(round((START_PRICE - st["price"]) / START_PRICE * 100))
        return (
            "\n\n🎉 **成交！%.1f 元/斤**（砍下 %.1f 元，-%d%%）。"
            "老王把黄瓜往袋里一塞，又抓了把小葱扔进去：「拿走拿走，别说是我给的价。」"
            " ｜ 用了 %d 轮" % (st["price"], saved, pct, st["round"])
        )
    if status == "broke":
        return (
            "\n\n💥 **谈崩了**：情绪 %d/100，已到掀摊线（≤%d）。"
            "老王把塑料布一掀：「不卖了不卖了，你上别家！」" % (st["mood"], BROKE_MOOD)
        )
    return (
        "\n\n⏰ **收摊了，没谈成**：%d 轮用完，最后 %.1f 元/斤，没到 %.1f 的成交线。"
        "老王收摊前撂下一句：「明儿早点来。」" % (st["round"], st["price"], TARGET_PRICE)
    )


def settle(st: dict, tactic: str, intel_hint: str = "") -> dict:
    """推进一轮，返回台词；st 就地更新。

    intel_hint 由服务端从 seekdb 的行情表读出后传入（只影响台词，不影响数值）。
    """
    rule = TACTICS[tactic]
    count_before = int(st["tactics"].get(tactic, 0))
    decay = decay_of(count_before)          # R6

    old_price = float(st["price"])
    gap = max(0.0, old_price - FLOOR_PRICE)           # R2：让价空间按底价算
    drop = gap * BASE_RATIO * rule["coef"] * decay    # R5
    if st["mood"] < LOW_MOOD_LINE:                    # R7：按本轮开始时的情绪
        drop *= LOW_MOOD_RATIO
    if st["intel"] >= 1:                              # R10：只对已解锁的后续轮次生效
        drop += INTEL_BONUS * decay

    first_intel = 0
    if tactic == "试探行情" and not st["intel"]:       # R9：一次性，且情绪不变
        st["intel"] = 1
        first_intel = 1
        drop += INTEL_ONE_OFF

    new_price = round1(max(FLOOR_PRICE, old_price - drop))          # R2 + 四舍五入

    mood = st["mood"] + rule["mood"]                  # 情绪只有招式自身的增减
    mood = max(0, min(100, mood))

    if mood >= SOFTEN_MOOD and st["round"] + 1 >= 3:  # R8：按本轮结算后的情绪
        new_price = round1(max(FLOOR_PRICE, new_price - SOFTEN_PRICE))

    st["round"] += 1
    st["price"] = new_price
    st["mood"] = mood
    st["tactics"][tactic] = count_before + 1

    # 结局判定：掀摊优先于成交（R11 > R3），最后才是超时（R12）
    if mood <= BROKE_MOOD:
        st["status"] = "broke"
    elif new_price <= TARGET_PRICE + 1e-9:
        st["status"] = "deal"
    elif st["round"] >= MAX_ROUND:
        st["status"] = "timeout"
    else:
        st["status"] = "open"

    lines = [rule["line"]]
    if first_intel:
        lines.append("他往筐后一指：「%s」" % (intel_hint or "这批黄瓜进价七块二，我卖你十二，你说我黑不黑？"))
    if decay < 1.0 and st["status"] == "open":
        lines.append("（「%s」你已经用了 %d 次，本轮让价只剩 ×%.2f。）"
                     % (tactic, count_before + 1, decay))
    lines.append(
        "💰 **%.1f 元/斤**｜成交线 ≤ %.1f｜第 %d/%d 轮｜😤 老王情绪 %s %d/100"
        % (st["price"], TARGET_PRICE, st["round"], MAX_ROUND, band(st["mood"]), st["mood"])
    )
    if st["intel"]:
        lines.append("🕵 已套出进货价：每轮额外让 %.2f 元（乘本轮衰减）" % INTEL_BONUS)
    lines.append(_closing(st, st["status"]).strip())
    return {"reply": "\n\n".join(x for x in lines if x), "state": st}

# -*- coding: utf-8 -*-
"""按 README「规则表」逐条独立实现的参考结算器。

这个文件存在的唯一目的：证明「文档声明」和「实际代码」是同一套规则。

它 **不 import game.py**，常量、公式、判定顺序全部照 README 的 R1–R13 重新写一遍，
连四舍五入也自己实现。rules_check.py 会把 game.settle 与 rules_ref.settle 放在
8 种招式、6 轮的全部 262144 条序列上逐轮比对：价格、情绪、轮次、结局必须全等。
只要 game.py 里偷偷多了一条没写进文档的规则（上一版就是这种情况），这里立刻会炸。

对应关系（README 编号 → 本文件实现）：
R1 new_game() / R2 FLOOR / R3 TARGET / R4 MAX_ROUND / R5 BASE_RATIO+COEF
R6 decay() / R7 LOW_MOOD / R8 SOFTEN / R9 INTEL_ONE_OFF / R10 INTEL_BONUS
R11 BROKE / R12 第 6 轮 timeout / R13 由调用方（app.py）重开，本文件不涉及
"""
from decimal import Decimal, ROUND_HALF_UP

# ---- R1 ----
START_PRICE = 12.0
START_MOOD = 50
# ---- R2 ----
FLOOR = 7.2
# ---- R3 ----
TARGET = 8.0
# ---- R4 ----
MAX_ROUND = 6
# ---- R5 ----
BASE_RATIO = 0.35
COEF = {"对比邻摊": 1.30, "挑刺": 1.10, "走人威胁": 1.40, "套近乎": 0.90,
        "卖惨": 0.80, "硬砍": 0.50, "试探行情": 0.55, "认错缓和": 0.70}
# 招式情绪增减（README 招式表右列）
MOOD_DELTA = {"对比邻摊": -6, "挑刺": -4, "走人威胁": -12, "套近乎": 8,
              "卖惨": 2, "硬砍": -8, "试探行情": 0, "认错缓和": 6}
# ---- R7 ----
LOW_MOOD_LINE = 35
LOW_MOOD_RATIO = 0.6
# ---- R8 ----
SOFTEN_MOOD = 85
SOFTEN_AMOUNT = 0.3
# ---- R9 / R10 ----
INTEL_ONE_OFF = 0.2
INTEL_BONUS = 0.15
# ---- R11 ----
BROKE = 20


def half_up(x):
    """R2/R5/R8 里说的「四舍五入到 0.1」，独立实现。"""
    return float(Decimal(repr(round(float(x), 6))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def new_game():
    return {"round": 0, "price": START_PRICE, "mood": START_MOOD,
            "intel": 0, "used": {}, "status": "open"}


def decay(times_before):
    """R6：第 times_before+1 次用的衰减。"""
    if times_before < 1:
        return 1.0
    return max(0.25, 1 - 0.4 * times_before)


def settle(st, tactic):
    """按 README 的结算顺序推进一轮（就地修改并返回 st）。"""
    n = st["used"].get(tactic, 0)
    d = decay(n)                                   # R6

    # R5 基础让价，R7 情绪低时打折，R10 已解锁情报的后续轮次加成
    drop = (st["price"] - FLOOR) * BASE_RATIO * COEF[tactic] * d   # R5
    if st["mood"] < LOW_MOOD_LINE:                                 # R7
        drop = drop * LOW_MOOD_RATIO
    if st["intel"] == 1:                                           # R10
        drop = drop + INTEL_BONUS * d
    if tactic == "试探行情" and st["intel"] == 0:                   # R9
        drop = drop + INTEL_ONE_OFF
        st["intel"] = 1

    price = half_up(max(FLOOR, st["price"] - drop))                # R2
    mood = max(0, min(100, st["mood"] + MOOD_DELTA[tactic]))       # 情绪：只有招式增减

    rnd = st["round"] + 1
    if mood >= SOFTEN_MOOD and rnd >= 3:                           # R8
        price = half_up(max(FLOOR, price - SOFTEN_AMOUNT))

    st["round"] = rnd
    st["price"] = price
    st["mood"] = mood
    st["used"][tactic] = n + 1

    if mood <= BROKE:                                              # R11 优先
        st["status"] = "broke"
    elif price <= TARGET:                                          # R3
        st["status"] = "deal"
    elif rnd >= MAX_ROUND:                                         # R12
        st["status"] = "timeout"
    else:
        st["status"] = "open"
    return st

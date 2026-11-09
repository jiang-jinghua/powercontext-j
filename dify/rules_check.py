# -*- coding: utf-8 -*-
"""规则一致性自检：README「规则表」= 代码实际行为。

做法分两层，避免"自己证明自己"：

第一层（逐条）：每条规则写一个最小场景，把**手算期望值**（直接照 README 公式推）
            和 game.settle 的真实输出比对；
第二层（穷举）：rules_ref.py 是完全照 README 重写的第二套实现，把
            8 种招式 × 6 轮 = 262144 条序列，逐轮比对价格/情绪/轮次/结局，
            任何一条不一致都会打印出来。

上一版（v2）就是被这一层抓住的：代码里多了一条"首次套出情报情绪 +8"，
README 没写，于是按声明算第 4 轮是 7.8 元、实际跑出 7.7 元——"结果与声明规则不符"。

用法：python rules_check.py   → 全部 PASS 时退出码 0
"""
import itertools
import sys

import game
import rules_ref

RESULTS = []


def check(rule_id, desc, ok, detail=""):
    RESULTS.append((rule_id, desc, bool(ok), detail))
    return ok


def play(actions, engine="game"):
    """按顺序出招，返回逐轮快照。engine 选 game / ref。"""
    if engine == "ref":
        st = rules_ref.new_game()
        out = []
        for act in actions:
            if st["status"] != "open":
                break
            rules_ref.settle(st, act)
            out.append({"round": st["round"], "price": st["price"],
                        "mood": st["mood"], "status": st["status"]})
        return out
    st = game.new_state()
    out = []
    for act in actions:
        if st["status"] != "open":
            break
        game.settle(st, act, "")
        out.append({"round": st["round"], "price": st["price"],
                    "mood": st["mood"], "status": st["status"]})
    return out


def main():
    # ---------- R1 开局 ----------
    st = game.new_state()
    check("R1", "开局 12.0 元 / 情绪 50 / 第 0 轮 / 未解锁情报",
          st["price"] == 12.0 and st["mood"] == 50 and st["round"] == 0 and st["intel"] == 0,
          "price=%.1f mood=%d round=%d intel=%d" % (st["price"], st["mood"], st["round"], st["intel"]))

    # ---------- R5 八种招式首轮让价（手算） ----------
    bad = []
    for tac in game.CHOICES:
        price = 12.0
        drop = (price - 7.2) * 0.35 * rules_ref.COEF[tac]          # R5，decay=1
        if tac == "试探行情":
            drop += 0.2                                            # R9
        want = rules_ref.half_up(max(7.2, price - drop))
        got = play([tac])[0]["price"]
        if abs(got - want) > 1e-9:
            bad.append("%s 手算 %.1f / 实际 %.1f" % (tac, want, got))
    check("R5", "8 种招式首轮让价 = 12 − (12−7.2)×0.35×系数", not bad,
          "；".join(bad) or "8 条全部吻合（含 R9 的 0.2）")

    # ---------- R6 重复衰减 ----------
    seq = play(["套近乎"] * 3)
    d = [rules_ref.decay(i) for i in range(3)]
    want2 = rules_ref.half_up(seq[0]["price"] - (seq[0]["price"] - 7.2) * 0.35 * 0.90 * 0.6)
    want3 = rules_ref.half_up(seq[1]["price"] - (seq[1]["price"] - 7.2) * 0.35 * 0.90 * 0.25)
    check("R6", "同一招第 2 次 ×0.6、第 3 次起 ×0.25（下限）",
          d == [1.0, 0.6, 0.25] and seq[1]["price"] == want2 and seq[2]["price"] == want3,
          "衰减 %s；第2轮 %.1f(期望 %.1f) 第3轮 %.1f(期望 %.1f)"
          % (d, seq[1]["price"], want2, seq[2]["price"], want3))

    # ---------- R7 情绪 < 35 打折 ----------
    # 对比邻摊(-6) → 走人威胁(-12) 之后情绪 = 32 < 35
    seq = play(["对比邻摊", "走人威胁"])
    mood_before, p_before = seq[-1]["mood"], seq[-1]["price"]
    nxt = play(["对比邻摊", "走人威胁", "挑刺"])[-1]
    want = rules_ref.half_up(p_before - (p_before - 7.2) * 0.35 * 1.10 * 1.0 * 0.6)
    check("R7", "本轮开始时情绪 < 35 → 让价再 ×0.6", mood_before < 35 and nxt["price"] == want,
          "情绪 %d → 期望 %.1f 实际 %.1f" % (mood_before, want, nxt["price"]))

    # ---------- R8 情绪 ≥85 且第 3 轮起抹零 0.3 ----------
    seq = play(["套近乎"] * 5)
    hit = [x for x in seq if x["mood"] >= 85 and x["round"] >= 3]
    prev = seq[-2]
    base = rules_ref.half_up(prev["price"] - (prev["price"] - 7.2) * 0.35 * 0.90 * rules_ref.decay(4))
    want = rules_ref.half_up(max(7.2, base - 0.3))
    check("R8", "结算后情绪 ≥85 且轮次 ≥3 → 再减 0.3", bool(hit) and seq[-1]["price"] == want,
          "第 5 轮情绪 %d，期望 %.1f 实际 %.1f" % (seq[-1]["mood"], want, seq[-1]["price"]))

    # ---------- R9 首次套出情报：多让 0.2，情绪不变 ----------
    one = play(["试探行情"])[0]
    st = game.new_state()
    game.settle(st, "试探行情", "")
    check("R9", "首次「试探行情」多让 0.2 且情绪不变、解锁情报",
          one["mood"] == 50 and st["intel"] == 1
          and one["price"] == rules_ref.half_up(12.0 - (12.0 - 7.2) * 0.35 * 0.55 - 0.2),
          "情绪 %d（应 50），intel=%d，价格 %.1f" % (one["mood"], st["intel"], one["price"]))

    # ---------- R10 解锁后每轮额外 0.15×衰减 ----------
    seq = play(["试探行情", "挑刺"])
    prev = seq[0]
    want = rules_ref.half_up(prev["price"] - ((prev["price"] - 7.2) * 0.35 * 1.10 + 0.15))
    check("R10", "解锁情报后的轮次额外让 0.15×衰减", seq[1]["price"] == want,
          "期望 %.1f 实际 %.1f" % (want, seq[1]["price"]))

    # ---------- 穷举一遍，同时统计三种结局 ----------
    deal_bad, broke_bad, low, over = [], [], [], []
    deal_cnt = broke_cnt = 0
    for combo in itertools.product(game.CHOICES, repeat=6):
        st = game.new_state()
        for t in combo:
            if st["status"] != "open":
                break
            game.settle(st, t, "")
        if st["status"] == "deal":
            deal_cnt += 1
            if st["price"] > 8.0 + 1e-9:
                deal_bad.append((combo, st["price"]))
        if st["status"] == "broke":
            broke_cnt += 1
            if st["mood"] > 20:
                broke_bad.append((combo, st["mood"], st["price"]))
        if st["price"] < 7.2 - 1e-9:
            low.append((combo, st["price"]))
        if st["round"] > 6:
            over.append((combo, st["round"]))
    check("R3", "穷举 8^6：deal 必然价格 ≤8.0", not deal_bad,
          "成交 %d 例，越界 %d 例" % (deal_cnt, len(deal_bad)))
    check("R11", "穷举 8^6：broke 必然情绪 ≤20（优先于成交）", not broke_bad,
          "掀摊 %d 例，越界 %d 例" % (broke_cnt, len(broke_bad)))
    check("R2", "穷举 8^6：价格始终 ≥7.2（底价）", not low, "越界 %d 例" % len(low))
    check("R4", "穷举 8^6：轮次 ≤6，第 6 轮后必为终局", not over, "越界 %d 例" % len(over))

    # ---------- 第二层：声明参考实现 vs 实际实现，逐轮穷举比对 ----------
    mismatches = []
    rounds_compared = 0
    for combo in itertools.product(game.CHOICES, repeat=6):
        st = game.new_state()
        ref = rules_ref.new_game()
        for t in combo:
            if st["status"] != "open":
                break
            game.settle(st, t, "")
            rules_ref.settle(ref, t)
            rounds_compared += 1
            if (st["round"] != ref["round"] or abs(st["price"] - ref["price"]) > 1e-9
                    or st["mood"] != ref["mood"] or st["status"] != ref["status"]):
                mismatches.append(("·".join(combo[:st["round"]]), st["price"], ref["price"],
                                   st["mood"], ref["mood"], st["status"], ref["status"]))
                break
    check("ALL", "声明复算(rules_ref) vs 实现(game) 逐轮全等", not mismatches,
          "比对 %d 个轮次，不一致 %d 处%s"
          % (rounds_compared, len(mismatches),
             "" if not mismatches else "；首个不一致：" + str(mismatches[0])))

    # ---------- 输出文案必须与 status 一致（不许"还在砍价"却说"收摊了"） ----------
    text_bad = []
    for combo in itertools.product(game.CHOICES, repeat=3):
        st = game.new_state()
        for t in combo:
            if st["status"] != "open":
                break
            reply = game.settle(st, t, "")["reply"]
            marks = {"deal": "成交！", "broke": "谈崩了", "timeout": "收摊了，没谈成"}
            for status, mark in marks.items():
                should = (st["status"] == status)
                if (mark in reply) != should:
                    text_bad.append((t, st["status"], mark, reply[:60]))
                    break
            else:
                if st["status"] == "open" and ("成交！" in reply or "谈崩" in reply or "收摊" in reply):
                    text_bad.append((t, st["status"], "open 却出现终局文案", reply[:60]))
    check("TXT", "回复文案与 status 一致（open 不出现终局文案，终局只出现对应文案）",
          not text_bad, "检查 512 条序列，异常 %d 处%s"
          % (len(text_bad), "" if not text_bad else "：" + str(text_bad[0])))

    # ---------- 三个剧本（文档里的例子）必须两套实现一致 ----------
    scripts = {
        "剧本A": ["对比邻摊", "走人威胁", "试探行情", "套近乎"],
        "剧本B": ["走人威胁", "走人威胁", "走人威胁"],
        "剧本C": ["卖惨"] * 6,
    }
    for name, acts in scripts.items():
        g = play(acts, "game")
        r = play(acts, "ref")
        check(name, "%s 与参考实现逐轮一致" % name, g == r,
              " → ".join("%.1f元/mood%d/%s" % (x["price"], x["mood"], x["status"]) for x in g))

    print("规则一致性自检：README 声明（rules_ref.py 独立重写）↔ game.py 实际实现")
    print("-" * 100)
    for rid, desc, ok, detail in RESULTS:
        print("%-5s %-44s %s  %s" % (rid, desc, "PASS" if ok else "FAIL", detail))
    print("-" * 100)
    failed = [r for r in RESULTS if not r[2]]
    print("共 %d 项检查，通过 %d，失败 %d；穷举序列 8^6=262144 条"
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    if not failed:
        print("结论：文档声明的规则可以逐字复算出实现的全部结果，不存在未声明的隐藏规则。")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

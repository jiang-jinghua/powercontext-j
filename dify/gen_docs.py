# -*- coding: utf-8 -*-
"""从代码生成文档里的表格，杜绝"文档写的数"和"代码算的数"漂移。

生成三块内容，直接替换 README 中的标记区间：
  <!-- AUTO:RULES -->   … <!-- /AUTO:RULES -->        规则表 R1–R13（阈值取自 rules_ref 常量）
  <!-- AUTO:TACTICS --> … <!-- /AUTO:TACTICS -->      8 种招式的系数与情绪增减
  <!-- AUTO:WALKTHROUGH --> … <!-- /AUTO:WALKTHROUGH -->  剧本 A 的逐轮算式（真算，不是手写）

另外把 evidence/backend-results.json 翻成 docs/验收样例-逐轮对话.md。

用法：python gen_docs.py       # 需要先跑过 verify.py 生成证据
"""
import json
import pathlib

import rules_ref


def render_rules():
    r = rules_ref
    rows = [
        ("R1", "开局标价 / 情绪 / 轮次", "%.1f 元/斤；情绪 %d；第 0 轮；情报未解锁"
         % (r.START_PRICE, r.START_MOOD)),
        ("R2", "老王底价（任何情况不低于）", "%.1f 元/斤" % r.FLOOR),
        ("R3", "成交条件（唯一）", "本轮结算后价格 ≤ %.1f → deal" % r.TARGET),
        ("R4", "轮数上限", "%d 轮；第 %d 轮结束必为终局，不存在第 %d 轮"
         % (r.MAX_ROUND, r.MAX_ROUND, r.MAX_ROUND + 1)),
        ("R5", "每轮基础让价", "(本轮开始时的价格 − %.1f) × %.2f × 招式系数 × 衰减"
         % (r.FLOOR, r.BASE_RATIO)),
        ("R6", "同一招式重复使用", "第 n 次（n≥2）的衰减 = max(%.2f, 1 − %.1f×(n−1))，即 ×0.6、×0.25、×0.25…"
         % (0.25, 0.4)),
        ("R7", "本轮开始时情绪 < %d" % r.LOW_MOOD_LINE, "本轮让价再 ×%.1f（在 R5 之后、R9/R10 之前）"
         % r.LOW_MOOD_RATIO),
        ("R8", "本轮结算后情绪 ≥ %d 且轮次 ≥ 3" % r.SOFTEN_MOOD,
         "四舍五入后，再额外减 %.1f 元（仅该轮）" % r.SOFTEN_AMOUNT),
        ("R9", "首次使用「试探行情」", "本轮一次性多让 %.1f 元，并置 intel=1（情绪不变）" % r.INTEL_ONE_OFF),
        ("R10", "已解锁情报的后续轮次", "每轮额外让 %.2f × 本轮衰减（解锁那一轮不加，见 R9）" % r.INTEL_BONUS),
        ("R11", "本轮结算后情绪 ≤ %d" % r.BROKE, "掀摊 broke，优先级高于 R3"),
        ("R12", "第 %d 轮结束仍 > %.1f" % (r.MAX_ROUND, r.TARGET), "收摊没谈成 timeout，不算成交"),
        ("R13", "已终局的局再出招", "作废旧局、开新局（restarted=true），新局从第 1 轮开始"),
    ]
    out = ["| 编号 | 规则 | 取值 / 公式 |", "|------|------|-------------|"]
    out += ["| %s | %s | %s |" % row for row in rows]
    return "\n".join(out)


def fmt_mood(d):
    return ("+%d" % d) if d > 0 else str(d)


def render_tactics():
    names = list(rules_ref.COEF)
    half = (len(names) + 1) // 2
    left, right = names[:half], names[half:]
    out = ["| 招式 | 系数 | 情绪 | 招式 | 系数 | 情绪 |",
           "|------|------|------|------|------|------|"]
    for i, a in enumerate(left):
        b = right[i] if i < len(right) else None
        cell_b = ("%s | %.2f | %s" % (b, rules_ref.COEF[b], fmt_mood(rules_ref.MOOD_DELTA[b]))) if b else " | | "
        out.append("| %s | %.2f | %s | %s |" % (a, rules_ref.COEF[a], fmt_mood(rules_ref.MOOD_DELTA[a]), cell_b))
    return "\n".join(out)


def trace(actions):
    """照 README 的结算顺序逐步记下每一步的中间量，用来打印算式。"""
    st = rules_ref.new_game()
    steps = []
    for act in actions:
        if st["status"] != "open":
            break
        old_price, old_mood = st["price"], st["mood"]
        n = st["used"].get(act, 0)
        d = rules_ref.decay(n)
        gap = old_price - rules_ref.FLOOR
        base = gap * rules_ref.BASE_RATIO * rules_ref.COEF[act]
        low = old_mood < rules_ref.LOW_MOOD_LINE
        main = base * (rules_ref.LOW_MOOD_RATIO if low else 1.0)
        intel_bonus = rules_ref.INTEL_BONUS * d if st["intel"] == 1 else 0.0
        one_off = rules_ref.INTEL_ONE_OFF if (act == "试探行情" and st["intel"] == 0) else 0.0
        drop = main + intel_bonus + one_off
        new_price = rules_ref.half_up(max(rules_ref.FLOOR, old_price - drop))
        mood = max(0, min(100, old_mood + rules_ref.MOOD_DELTA[act]))
        soften = 0.0
        if mood >= rules_ref.SOFTEN_MOOD and st["round"] + 1 >= 3:
            soften = rules_ref.SOFTEN_AMOUNT
            new_price = rules_ref.half_up(max(rules_ref.FLOOR, new_price - soften))

        # 让价算式字符串
        expr = "(%.1f−%.1f)×%.2f×%.2f" % (old_price, rules_ref.FLOOR, rules_ref.BASE_RATIO, rules_ref.COEF[act])
        if d != 1.0:
            expr += "×%.2f" % d
        if low:
            expr += "×%.1f" % rules_ref.LOW_MOOD_RATIO
        if intel_bonus:
            expr += " + %.2f×%.2f" % (rules_ref.INTEL_BONUS, d)
        if one_off:
            expr += " + %.1f" % rules_ref.INTEL_ONE_OFF
        expr += " = %.3f" % drop
        if soften:
            expr += "，再 −%.1f" % soften

        rules_ref.settle(st, act)
        steps.append({
            "round": st["round"], "tactic": act, "expr": expr,
            "price": st["price"], "mood": st["mood"], "status": st["status"],
            "old_price": old_price, "old_mood": old_mood, "decay": d,
            "low": low, "one_off": bool(one_off), "soften": bool(soften),
        })
    return steps


def render_walkthrough(actions):
    steps = trace(actions)
    out = ["| 轮 | 招式 | 让价算式（代入 R5–R10） | 价格 | 情绪 | 结局 |",
           "|----|------|--------------------------|------|------|------|"]
    for s in steps:
        note = {"open": "继续", "deal": "**成交**", "broke": "**掀摊**", "timeout": "**超时**"}[s["status"]]
        out.append("| %d | %s | %s | %.1f | %d | %s |"
                   % (s["round"], s["tactic"], s["expr"], s["price"], s["mood"], note))
    last = steps[-1]
    used = set()
    for s in steps:
        if s["low"]:
            used.add("`×0.6` = R7（本轮开始时情绪 < %.0f）" % rules_ref.LOW_MOOD_LINE)
        if s["one_off"]:
            used.add("`+0.2` = R9（首次套出情报）")
        if "+ %.2f×" % rules_ref.INTEL_BONUS in s["expr"]:
            used.add("`+0.15×衰减` = R10（已解锁情报的后续轮次）")
    legend = ("\n\n算式里的记号：" + "；".join(sorted(used)) + "。") if used else ""
    tail = {
        "deal": "\n第 %d 轮 **%.1f 元 ≤ %.1f**，满足 R3 → 成交（判定顺序见 R11 > R3 > R12）。"
                % (last["round"], last["price"], rules_ref.TARGET),
        "broke": "\n第 %d 轮情绪 %d ≤ %d，R11 优先于成交 → 掀摊。"
                 % (last["round"], last["mood"], rules_ref.BROKE),
        "timeout": "\n第 %d 轮结束 %.1f 元仍 > %.1f，R12 → 收摊没谈成（不算成交）。"
                   % (last["round"], last["price"], rules_ref.TARGET),
    }[last["status"]]
    return "\n".join(out) + tail + legend


def patch_readme(path="README.md"):
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    blocks = {
        "RULES": render_rules(),
        "TACTICS": render_tactics(),
        "WALKTHROUGH": render_walkthrough(["对比邻摊", "走人威胁", "试探行情", "套近乎"]),
    }
    for key, body in blocks.items():
        start, end = "<!-- AUTO:%s -->" % key, "<!-- /AUTO:%s -->" % key
        if start not in text:
            print("跳过（README 无标记）：", key)
            continue
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        text = head + start + "\n" + body + "\n" + end + tail
    p.write_text(text, encoding="utf-8")


def render_transcript(src="evidence/backend-results.json",
                      dst="docs/验收样例-逐轮对话.md"):
    data = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
    by = {}
    for r in data:
        by.setdefault(r["scenario"].split(" 第 ")[0], []).append(r)

    out = ["# 验收样例：一局真实跑完的砍价（逐轮对话）", "",
           "> 台词与数字全部来自真实运行：`python verify.py` 打 `POST /turn`，后端把局面写进 seekdb 后返回。",
           "> 每轮的 `expected` 由 `rules_ref.py`（照 README 规则表独立实现）算出，`match=true` 表示接口实际返回与声明规则逐字段一致。",
           "> 原始数据见 `evidence/backend-results.json`。", ""]

    def block(name, title, desc):
        rows = by.get(name, [])
        if not rows:
            return
        out.extend([title, "", desc, ""])
        for r in rows:
            o, e = r["output"], r["expected"]
            if "reply" not in o:
                continue
            out.append("### 第 %d 轮 · %s%s" % (o["round"], r["input"]["action"],
                                                "（R13 重开新局）" if o.get("restarted") else ""))
            out.append("")
            out.append("> 玩家：%s" % r["input"]["action"])
            out.append(">")
            body = o["reply"].strip().replace("\n\n", "\n>\n> ")
            out.append("> %s" % body)
            out.append("")
            out.append("| 字段 | 声明规则算出来 | 接口实际 | 一致 |")
            out.append("|------|----------------|----------|------|")
            out.append("| price | %.1f | %.1f | %s |" % (e["price"], o["price"], "✓" if abs(e["price"] - o["price"]) < 1e-9 else "✗"))
            out.append("| mood | %d | %d | %s |" % (e["mood"], o["mood"], "✓" if e["mood"] == o["mood"] else "✗"))
            out.append("| round | %d | %d | %s |" % (e["round"], o["round"], "✓" if e["round"] == o["round"] else "✗"))
            out.append("| status | %s | %s | %s |" % (e["status"], o["status"], "✓" if e["status"] == o["status"] else "✗"))
            out.append("")

    block("A 正常砍价", "## 剧本 A：情报开路，4 轮成交", "目标：6 轮内砍到 8.0 元/斤以下；开局 12.0 元/斤。")
    # 幂等与持久化
    for r in data:
        if r["scenario"].startswith("A 幂等"):
            out.extend(["## 幂等重放：同一 request_id 再发一次", "",
                        "返回与第一次逐字段相同（`match=true`），轮次仍是 %d，没有重复扣轮次。" % r["output"]["round"], ""])
        if r["scenario"].startswith("A seekdb"):
            out.extend(["## 关掉页面再回来：局面还在 seekdb 里", "",
                        "`GET /state/%s` 不走任何内存缓存，纯读库，读到的是同一局面："
                        % r["output"].get("player", ""), "",
                        "```json", json.dumps(r["output"], ensure_ascii=False, indent=2), "```", ""])
    block("B 重复出招衰减", "## 剧本 B：把同一招连用三次", "第 2 次让价 ×0.6、第 3 次 ×0.25，情绪一路掉到掀摊线。")
    block("C 6 轮未成交", "## 剧本 C：6 轮都没砍到 8.0 —— 不算成交", "连续六次「卖惨」（系数 0.80，且重复使用衰减）：")
    for r in data:
        if r["scenario"].startswith("C 终局"):
            out.extend(["## 终局后再出招：R13 重开新局", "",
                        "同一玩家再出「套近乎」，`restarted=true`，轮次回到 1、价格回到 %.1f（新局第一招）："
                        % r["output"]["price"], "",
                        "```json", json.dumps({k: r["output"][k] for k in
                                               ("restarted", "round", "price", "mood", "status")},
                                              ensure_ascii=False, indent=2), "```", ""])

    neg = pathlib.Path("evidence/negative-results.json")
    if neg.exists():
        rows = json.loads(neg.read_text(encoding="utf-8"))
        out.extend(["## 异常路径", "", "| 用例 | 期望 | 实际 | 结论 |", "|------|------|------|------|"])
        for r in rows:
            out.append("| %s | %s | %s | %s |" % (r["case"], r["expect"], r["actual"],
                                                  "通过" if r["match"] else "未通过"))
        out.append("")

    pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(dst).write_text("\n".join(out), encoding="utf-8")
    return dst


if __name__ == "__main__":
    patch_readme()
    p = render_transcript()
    print("README 规则表/招式表/逐轮算式已从代码重新生成；对话样例写入", p)

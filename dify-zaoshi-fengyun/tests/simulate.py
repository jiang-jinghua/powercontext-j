# -*- coding: utf-8 -*-
"""剧本回放：模拟摊主大脑（LLM 节点）的输出，驱动结算内核跑完整局，
生成 evidence/transcript-settle-sim.md —— 用于验证「跨轮状态 + 招式衰减 + 结局判定」
这一确定性部分的真实行为（LLM 台词部分需在 Dify 实机验证）。

用法: python tests/simulate.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "build"))
import settle_code as sc  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(HERE), "evidence")


def ai(reply, tactic, intel_hit=0, note=""):
    return json.dumps({"reply": reply, "tactic": tactic,
                       "intel_hit": intel_hit, "note": note}, ensure_ascii=False)


SCRIPTS = [
    {
        "name": "剧本A · 标准砍价（情报开局，4 轮成交）",
        "users": [
            "「老板，这黄瓜咋卖？能便宜点不？」",
            "「东头李姐那边才 9 块 5，你这 12 块是金子做的？」",
            "「你看看你这瓜把都黄了，还 12 块？」",
            "「行行行，我说错了，你给个痛快价吧。」",
        ],
        "turns": [
            ai("老王把烟往耳朵上一别，蹲下来拍拍黄瓜：「买几斤啊，这大清早的？」", "试探行情", 0, "先探探虚实"),
            ai("老王眉毛一立：「李姐是李姐，我是我！她那瓜你掐掐，能响吗？」嘴上硬，手却把秤砣往外挪了挪。", "对比邻摊", 1, "这后生做过功课"),
            ai("老王把一捆瓜翻过来给你看：「瞧瞧这刺，瞧瞧这花！瓜跟瓜不一样，人跟人得讲个理不是？」", "挑刺", 0, "行家啊"),
            ai("老王把价签纸揉吧揉吧塞兜里，抓俩塑料袋：「得，遇上懂行的了。两斤？给你挑最直溜的。」", "认错缓和", 0, "今天头一单"),
        ],
    },
    {
        "name": "剧本B · 重复威胁（4 轮掀摊，触发隐藏成就）",
        "users": [
            "「不买了，我走了啊。」",
            "「真走了啊，小张那儿才 8 块 8。」",
            "「再不便宜我可真走了。」",
            "「……你这没法聊了。」",
        ],
        "turns": [
            ai("老王一愣，秤杆都放下了：「哎哎，有话好好说嘛！」", "走人威胁", 0, "别啊别啊"),
            ai("老王撇撇嘴：「爱买不买……那你再瞅瞅，这瓜多水灵。」嘴硬，眼睛却瞟着你的腿。", "走人威胁", 0, "又来？"),
            ai("老王把烟头一掐：「你这是第三回了啊！我这儿不做吓唬人的生意！」", "走人威胁", 0, "烦了"),
            ai("老王把塑料布往上一掀：「得得得，您请，南门小张便宜，蔫瓜管够！」", "走人威胁", 0, "掀了"),
        ],
    },
    {
        "name": "剧本C · 软磨硬泡（不砍价只攒情绪，第 6 轮触发收摊友情价成交）",
        "users": [
            "「闺女想吃口脆黄瓜，老板给挑好的呗。」",
            "「家里就我一个人上班，这价实在有点够呛。」",
            "「老板你这秤准，人也实在，街坊都这么说。」",
            "「刚才是我不对，您别往心里去。」",
            "「您也不容易，我也不容易，互相担待呗。」",
            "「您给句痛快话，我明天还来。」",
        ],
        "turns": [
            ai("老王眯眼瞅瞅你：「家里娃儿想吃？那你挑，我给你留着最脆的。」手上一抖，秤杆翘了翘。", "套近乎", 0, "都是当爹妈的"),
            ai("老王摆摆手：「孩子爱吃就给孩子吃，可我这价……唉，进价搁那儿呢。」腰一弯，扶了把筐。", "卖惨", 0, "谁家不是过日子"),
            ai("老王打了个哈哈：「大姐你可饶了我吧，这价我卖了 23 年头一回听。」烟灰抖了抖。", "套近乎", 0, "这姐真会说"),
            ai("老王咂咂嘴：「嗯……这话说得我心里熨帖。要不你再看看别家？」嘴上支着，秤没动。", "认错缓和", 0, "松口了一半"),
            ai("老王给你把袋口撑开：「这么着，你别让我赔本，我也不让你白来，成不？」抹布擦了擦秤盘。", "卖惨", 0, "快了快了"),
            ai("老王看看天，又看看广播喇叭：「得，9 点的铃都快响了……你也是真有耐心。」把俩黄瓜放上了秤。", "认错缓和", 0, "这单成交吧"),
        ],
    },
]


def run_script(script) -> str:
    out = ["## %s\n" % script["name"], ""]
    state = ""
    for idx, llm in enumerate(script["turns"], 1):
        r = sc.main(llm, state)
        st = json.loads(r["state_json"])
        out.append("### 第 %d 轮" % idx)
        out.append("")
        out.append("**玩家**：%s" % script["users"][idx - 1])
        out.append("")
        out.append("**摊主大脑（LLM 节点模拟输出）**：`%s`" % llm)
        out.append("")
        out.append("**老王开口（应用回复）**：")
        out.append("")
        for line in r["reply"].split("\n"):
            out.append("> " + line)
        out.append("")
        out.append("**状态面板（写回会话变量，供下一轮摊主大脑读取）**：")
        out.append("")
        for line in r["panel"].split("\n"):
            out.append("> " + line)
        out.append("")
        out.append("<details><summary>状态 JSON（conversation.state_json）</summary>")
        out.append("")
        out.append("```json")
        out.append(json.dumps(st, ensure_ascii=False, indent=2))
        out.append("```")
        out.append("")
        out.append("</details>")
        out.append("")
        state = r["state_json"]
        if r["status"] != "open":
            out.append("**本局结束**：status = `%s`" % r["status"])
            out.append("")
    return "\n".join(out)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    header = [
        "# 运行证据 · 结算内核剧本回放（transcript）",
        "",
        "- 生成方式：`python tests/simulate.py`（纯标准库，无需 Dify 实例）",
        "- 覆盖范围：**结算台（代码节点）的全部确定性规则**——跨轮状态、招式衰减、情绪、情报、结局判定。",
        "- 未覆盖范围：摊主大脑（LLM 节点）的真实台词。上表中的台词为模拟输入，"
        "LLM 实际表现需在 Dify 实机按 docs/03-验证样例与运行证据.md 的用例采集。",
        "- 状态 JSON 通过「变量赋值」节点写回 Dify 会话变量 `conversation.state_json`，下一轮原样传回结算台——",
        "  本回放的调用序列与 DSL 中 `开始→情报检索→摊主大脑→结算台→局面写回→老王开口` 的数据流一致。",
        "",
    ]
    body = "\n".join(header) + "\n" + "\n".join(run_script(s) for s in SCRIPTS)
    path = os.path.join(OUT_DIR, "transcript-settle-sim.md")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    print("OK:", path)


if __name__ == "__main__":
    main()

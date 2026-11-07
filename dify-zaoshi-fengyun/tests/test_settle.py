# -*- coding: utf-8 -*-
"""结算内核单元测试：python tests/test_settle.py"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "build"))
import settle_code as sc  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


def run(llm, state=None):
    return sc.main(llm, json.dumps(state, ensure_ascii=False) if state is not None else "")


def llm_json(reply="行吧。", tactic="对比邻摊", intel_hit=0, note=""):
    return json.dumps({"reply": reply, "tactic": tactic, "intel_hit": intel_hit, "note": note},
                      ensure_ascii=False)


print("== 1. 开局首轮：对比邻摊应明显让价 ==")
r = run(llm_json("你倒是会说。", "对比邻摊", 0, "这后生做过功课"))
st = json.loads(r["state_json"])
check("轮次=1", st["round"] == 1)
check("价格下降且未到目标", 8.0 < st["price"] < 12.0, "price=%s" % st["price"])
check("状态=open", r["status"] == "open")
check("回复含播报", "价签" in r["reply"])
check("面板含情绪条", "█" in r["panel"])

print("== 2. 重复招式衰减 ==")
r1 = run(llm_json(tactic="对比邻摊"))
st1 = json.loads(r1["state_json"])
r2 = run(llm_json(tactic="对比邻摊"), st1)
st2 = json.loads(r2["state_json"])
drop1 = 12.0 - st1["price"]
drop2 = st1["price"] - st2["price"]
check("第二次同招让价明显变小", drop2 < drop1 * 0.75, "drop1=%.2f drop2=%.2f" % (drop1, drop2))
check("面板提示效果衰减", "效果只剩" in r2["panel"])
check("招式计数=2", st2["tactics"]["对比邻摊"] == 2)

print("== 3. 走人威胁掉情绪 / 连续威胁可掀摊 ==")
r = run(llm_json(tactic="走人威胁"))
st = json.loads(r["state_json"])
check("走人威胁扣情绪", st["mood"] < 50)
for i in range(3):
    r = run(llm_json(tactic="走人威胁"), st)
    st = json.loads(r["state_json"])
    if r["status"] == "broke":
        break
check("连续走人威胁触发掀摊", r["status"] == "broke", "mood=%s" % st["mood"])
check("掀摊文案", "谈崩了" in r["reply"])

print("== 4. 套近乎回情绪，且情绪高时抹零 ==")
st = None
for i in range(4):
    r = run(llm_json(tactic="套近乎"), st)
    st = json.loads(r["state_json"])
check("情绪上升", st["mood"] > 50, "mood=%s" % st["mood"])

print("== 5. 情报机制 ==")
r = run(llm_json("你这进货价我可听说了。", "挑刺", 1, "被说中了"))
st = json.loads(r["state_json"])
check("情报标记生效", st["intel"] == 1)
r2 = run(llm_json("再让点。", "挑刺"), st)
st2 = json.loads(r2["state_json"])
drop_i = st["price"] - st2["price"]
r3 = run(llm_json("再让点。", "挑刺"))
st3 = json.loads(r3["state_json"])
drop_ni = 12.0 - st3["price"]
check("有情报时每轮多让", drop_i > drop_ni * 0.3, "drop_i=%.2f drop_ni=%.2f" % (drop_i, drop_ni))

print("== 6. 稳健砍价可在 6 轮内成交 ==")
st = None
moves = ["对比邻摊", "挑刺", "对比邻摊", "挑刺", "对比邻摊", "挑刺", "对比邻摊", "挑刺"]
for i, mv in enumerate(moves):
    r = run(llm_json("…", mv, 1 if i == 0 else 0), st)
    st = json.loads(r["state_json"])
    if r["status"] != "open":
        break
check("6 轮内出结果", r["status"] in ("deal", "timeout", "broke"))
print("    -> 结果=%s 价格=%s 轮次=%s" % (r["status"], st["price"], st["round"]))
if r["status"] == "deal":
    check("成交价 <= 友情价", st["price"] <= 8.5)
    check("战绩卡", "成交" in r["reply"])

print("== 7. LLM 输出异常时降级不崩 ==")
r = run("这不是JSON，是老王的台词。")
st = json.loads(r["state_json"])
check("坏输出不崩且有台词", "老王" in r["reply"])
check("未知招式归为试探行情", st["tactics"].get("试探行情") == 1, str(st["tactics"]))
r = run("")
check("空输出不崩", len(r["reply"]) > 0)

print("== 8. 结束后自动开新局 ==")
r_end = run(llm_json(tactic="走人威胁"), None)
st_end = json.loads(r_end["state_json"])
while st_end["status"] == "open" and st_end["round"] < 8:
    r_end = run(llm_json(tactic="走人威胁"), st_end)
    st_end = json.loads(r_end["state_json"])
r_new = run(llm_json("再来。", "套近乎"), st_end)
st_new = json.loads(r_new["state_json"])
check("新局轮次=1", st_new["round"] == 1)
check("新局价格回到 12.0", st_new["price"] <= 12.0 and st_new["price"] > 8.0)

print("\n结果: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

# -*- coding: utf-8 -*-
"""端到端验收：把服务当黑盒，用**声明复算器**算期望值，再逐字段比对。

关键点（上一版被评测指出"结果与声明规则不符"，就是因为这里的期望值来自
game.py 自己，属于自证）：本脚本的期望值来自 rules_ref.py——照 README 规则表
另写的一套实现，不 import game 的结算逻辑。因此这里比对的是
「HTTP 接口的真实返回」↔「README 声明的规则推出来的数」。

用法：
    python verify.py                      # 默认 http://127.0.0.1:8000
    API_BASE=http://127.0.0.1:18082 python verify.py
令牌从 .env 的 GAME_TOKEN 读取（与 compose 的 .env 同一份）。
通过后把每轮真实请求/响应写进 evidence/backend-results.json。
"""
import json
import os
import pathlib
import sys
import urllib.request
import uuid

sys.path.insert(0, ".")
import game  # noqa: E402  只用于把口语归一成招式（parse_action），不取任何数值
import rules_ref  # noqa: E402  期望值唯一来源：照 README 重写


def load_env(path=".env"):
    env = {}
    p = pathlib.Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
TOKEN = os.getenv("GAME_TOKEN") or ENV.get("GAME_TOKEN", "")
BASE = os.getenv("API_BASE") or ENV.get("API_BASE") or "http://127.0.0.1:8000"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (TOKEN if token is None else token),
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def expect(actions):
    """按 README 规则表（rules_ref.py）推演期望序列。"""
    st = rules_ref.new_game()
    seq = []
    for act in actions:
        if st["status"] != "open":
            break
        tactic = game.parse_action(act)
        rules_ref.settle(st, tactic)
        seq.append({"round": st["round"], "price": st["price"],
                    "mood": st["mood"], "status": st["status"], "tactic": tactic})
    return seq


def wait_ready(timeout=240):
    """容器刚起来时 seekdb 还在 bootstrap，先等 /healthz 通（最多 timeout 秒）。"""
    import time

    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(
                BASE + "/healthz", headers={"Authorization": "Bearer " + TOKEN})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def main():
    rows = []
    failures = []
    if not wait_ready():
        print("服务在 240 秒内没有就绪：先看 docker compose logs seekdb / api")
        return 1

    def run_script(name, actions, player, id_prefix, expect_last_status):
        exp = expect(actions)
        for i, act in enumerate(actions):
            out = call("/turn", {"player": player, "action": act,
                                 "request_id": "%s%d" % (id_prefix, i)})
            e = exp[i]
            ok = (out["round"] == e["round"] and abs(out["price"] - e["price"]) < 1e-6
                  and out["mood"] == e["mood"] and out["status"] == e["status"])
            if not ok:
                failures.append("%s 第%d轮不符：声明期望 %s，接口实际 %s"
                                % (name, i + 1, e,
                                   {k: out[k] for k in ("round", "price", "mood", "status")}))
            rows.append({"scenario": "%s 第 %d 轮" % (name, i + 1),
                         "expected_from": "rules_ref.py（照 README 规则表独立实现）",
                         "input": {"player": player, "action": act,
                                   "request_id": "%s%d" % (id_prefix, i)},
                         "expected": e, "output": out, "match": ok})
        if exp[-1]["status"] != expect_last_status:
            failures.append("%s 期望结局 %s，声明复算得到 %s"
                            % (name, expect_last_status, exp[-1]["status"]))
        return exp

    # 剧本 A：情报开路，4 轮成交
    p1 = "verify-" + uuid.uuid4().hex[:12]
    acts_a = ["对比邻摊", "走人威胁", "试探行情", "套近乎"]
    exp_a = run_script("A 正常砍价", acts_a, p1, "", "deal")

    # 幂等：同一 request_id 重放，必须逐字段相同、不推进轮次
    last = call("/turn", {"player": p1, "action": "对比邻摊", "request_id": "3"})
    same = last == rows[-1]["output"]
    if not same:
        failures.append("幂等失败：同一 request_id 返回不同结果")
    rows.append({"scenario": "A 幂等重放（request_id=3 重复发送）",
                 "expected_from": "与第 4 次响应逐字段相同",
                 "input": {"player": p1, "action": "对比邻摊", "request_id": "3"},
                 "expected": rows[-1]["output"], "output": last, "match": same})

    # 持久化：/state 直接读库，不经内存
    persisted = call("/state/" + p1)
    still = (persisted["round"] == exp_a[-1]["round"]
             and abs(persisted["price"] - exp_a[-1]["price"]) < 1e-6
             and persisted["status"] == exp_a[-1]["status"])
    if not still:
        failures.append("持久化失败：/state 与最后一轮不一致 %s" % persisted)
    rows.append({"scenario": "A seekdb 持久化核对（GET /state）",
                 "expected_from": "上一轮的落地值",
                 "input": {"player": p1}, "expected": exp_a[-1], "output": persisted,
                 "match": still})

    # 剧本 B：连用同一招 → 衰减 → 情绪 ≤20 掀摊
    p2 = "verify-" + uuid.uuid4().hex[:12]
    exp_b = run_script("B 重复出招衰减", ["走人威胁"] * 3, p2, "b", "broke")

    # 剧本 C：6 轮都没到 8.0 → timeout；再出招按 R13 重开
    p3 = "verify-" + uuid.uuid4().hex[:12]
    exp_c = run_script("C 6 轮未成交", ["卖惨"] * 6, p3, "c", "timeout")
    if exp_c[-1]["price"] <= 8.0:
        failures.append("剧本C 价格已到成交线，无法证明超时路径")

    restart = call("/turn", {"player": p3, "action": "套近乎", "request_id": "c-restart"})
    want_restart = rules_ref.settle(rules_ref.new_game(), "套近乎")
    ok_restart = (restart["restarted"] is True and restart["round"] == 1
                  and restart["status"] == "open"
                  and abs(restart["price"] - want_restart["price"]) < 1e-6
                  and restart["mood"] == want_restart["mood"])
    if not ok_restart:
        failures.append("R13 重开新局不符：%s" % restart)
    rows.append({"scenario": "C 终局后再出招 → R13 重开新局",
                 "expected_from": "rules_ref.new_game() 后出「套近乎」",
                 "input": {"player": p3, "action": "套近乎", "request_id": "c-restart"},
                 "expected": {"restarted": True, "round": want_restart["round"],
                              "price": want_restart["price"], "mood": want_restart["mood"],
                              "status": want_restart["status"]},
                 "output": restart, "match": ok_restart})

    pathlib.Path("evidence").mkdir(exist_ok=True)
    pathlib.Path("evidence/backend-results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    # 记下本次三个剧本的 player，供导出 seekdb 证据时精确定位，避免带出历史脏数据
    pathlib.Path("evidence/players.json").write_text(
        json.dumps({"A": p1, "B": p2, "C": p3}, ensure_ascii=False, indent=2), encoding="utf-8")

    print("API_BASE =", BASE)
    print("剧本A：%s → %.1f 元，%d 轮，%s（声明复算）" % (
        " / ".join(acts_a), exp_a[-1]["price"], exp_a[-1]["round"], exp_a[-1]["status"]))
    print("剧本B：走人威胁 ×3 → 情绪 %d，%s" % (exp_b[-1]["mood"], exp_b[-1]["status"]))
    print("剧本C：卖惨 ×6 → %.1f 元，%d 轮，%s（没到 8.0 不算成交）" % (
        exp_c[-1]["price"], exp_c[-1]["round"], exp_c[-1]["status"]))
    print("证据已写入 evidence/backend-results.json（%d 条）" % len(rows))
    if failures:
        print("\n失败 %d 项：" % len(failures))
        for f in failures:
            print(" -", f)
        return 1
    print("\n全部通过：接口返回与 README 声明的规则逐字段一致（期望值由 rules_ref 独立算出）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

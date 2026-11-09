# -*- coding: utf-8 -*-
"""端到端验收脚本：把服务当成黑盒，用同一份 game.py 算出期望值再逐字段比对。

用法：
    python verify.py                      # 默认 http://127.0.0.1:8000
    API_BASE=http://127.0.0.1:18082 python verify.py
令牌从 .env 的 GAME_TOKEN 读取（与 compose 的 .env 同一份）。

通过后会把每轮真实请求/响应写进 evidence/backend-results.json。
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, ".")
import game  # noqa: E402


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


def expect(actions, hint=""):
    """本地按同一份内核推演，得到期望序列。"""
    st = game.new_state()
    seq = []
    for act in actions:
        if st["status"] != "open":
            break
        tactic = game.parse_action(act)
        game.settle(st, tactic, hint)
        seq.append({"round": st["round"], "price": st["price"],
                    "mood": st["mood"], "status": st["status"],
                    "tactic": tactic})
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

    # 剧本 A：正常砍价 —— 情报开路后 4 轮成交
    p1 = "verify-" + uuid.uuid4().hex[:12]
    acts_a = ["对比邻摊", "走人威胁", "试探行情", "套近乎"]
    exp_a = expect(acts_a)
    for i, act in enumerate(acts_a):
        out = call("/turn", {"player": p1, "action": act, "request_id": str(i)})
        e = exp_a[i]
        ok = (out["round"] == e["round"] and abs(out["price"] - e["price"]) < 1e-6
              and out["mood"] == e["mood"] and out["status"] == e["status"])
        if not ok:
            failures.append("剧本A 第%d轮不符：期望 %s，实际 %s" % (i + 1, e, out))
        rows.append({"scenario": "A 正常砍价", "input": {"player": p1, "action": act,
                     "request_id": str(i)}, "expected": e, "output": out, "match": ok})

    last = call("/turn", {"player": p1, "action": "对比邻摊", "request_id": "3"})
    same = last == rows[-1]["output"]
    if not same:
        failures.append("幂等失败：同一 request_id 返回不同结果")
    rows.append({"scenario": "A 幂等重放", "input": {"player": p1, "action": "对比邻摊",
                 "request_id": "3"}, "expected": rows[-1]["output"], "output": last,
                 "match": same})

    persisted = call("/state/" + p1)
    still = (persisted["round"] == exp_a[-1]["round"]
             and abs(persisted["price"] - exp_a[-1]["price"]) < 1e-6
             and persisted["status"] == exp_a[-1]["status"])
    if not still:
        failures.append("持久化失败：/state 与最后一轮不一致 %s" % persisted)
    rows.append({"scenario": "A seekdb 持久化核对", "input": {"player": p1},
                 "expected": exp_a[-1], "output": persisted, "match": still})

    # 剧本 B：连用同一招 -> 效果衰减 -> 情绪跌破 20 直接掀摊
    p2 = "verify-" + uuid.uuid4().hex[:12]
    acts_b = ["走人威胁", "走人威胁", "走人威胁"]
    exp_b = expect(acts_b)
    for i, act in enumerate(acts_b):
        out = call("/turn", {"player": p2, "action": act, "request_id": "b%d" % i})
        e = exp_b[i]
        ok = (out["round"] == e["round"] and abs(out["price"] - e["price"]) < 1e-6
              and out["mood"] == e["mood"] and out["status"] == e["status"])
        if not ok:
            failures.append("剧本B 第%d轮不符：期望 %s，实际 %s" % (i + 1, e, out))
        rows.append({"scenario": "B 重复出招衰减", "input": {"player": p2, "action": act,
                     "request_id": "b%d" % i}, "expected": e, "output": out, "match": ok})
    if exp_b[-1]["status"] != "broke":
        failures.append("剧本B 期望以掀摊收场，实际 %s" % exp_b[-1]["status"])

    pathlib.Path("evidence").mkdir(exist_ok=True)
    pathlib.Path("evidence/backend-results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("API_BASE =", BASE)
    print("剧本A：%s → %s 元，%d 轮，%s" % (
        " / ".join(acts_a), exp_a[-1]["price"], exp_a[-1]["round"], exp_a[-1]["status"]))
    print("剧本B：%s → 情绪 %d，%s" % (
        " / ".join(acts_b), exp_b[-1]["mood"], exp_b[-1]["status"]))
    print("证据已写入 evidence/backend-results.json（%d 条）" % len(rows))
    if failures:
        print("\n失败 %d 项：" % len(failures))
        for f in failures:
            print(" -", f)
        return 1
    print("\n全部通过：数值与本地内核逐字段一致，幂等与持久化均成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

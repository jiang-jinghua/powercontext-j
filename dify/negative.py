# -*- coding: utf-8 -*-
"""异常路径验收：鉴权、非法输入、状态不被脏写。

用法：python negative.py        # 或 API_BASE=... python negative.py
结果写入 evidence/negative-results.json。
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, ".")
from verify import BASE, TOKEN, call  # noqa: E402


def call_status(payload, token):
    req = urllib.request.Request(
        BASE + "/turn",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main():
    results = []
    ok = True
    p = "negative-" + uuid.uuid4().hex[:10]

    # 1) 令牌错误
    code = call_status({"player": p, "action": "对比邻摊", "request_id": "1"}, "bad-token")
    results.append({"case": "错误令牌", "expect": 401, "actual": code, "match": code == 401})
    ok &= code == 401

    # 2) 缺少令牌
    code = call_status({"player": p, "action": "对比邻摊", "request_id": "1"}, "")
    results.append({"case": "缺少令牌", "expect": 401, "actual": code, "match": code == 401})
    ok &= code == 401

    # 3) 非法招式 -> 422，且不推进轮次
    before = call("/turn", {"player": p, "action": "对比邻摊", "request_id": "n1"})
    code = call_status({"player": p, "action": "把黄瓜白送我", "request_id": "n2"}, TOKEN)
    after = call("/state/" + p)
    unchanged = (after["round"] == before["round"] and after["price"] == before["price"])
    results.append({"case": "非法招式", "expect": 422, "actual": code,
                    "match": code == 422 and unchanged,
                    "state_before": {"round": before["round"], "price": before["price"]},
                    "state_after": {"round": after["round"], "price": after["price"]}})
    ok &= code == 422 and unchanged

    # 4) 缺字段 -> 422（Pydantic 校验）
    req = urllib.request.Request(
        BASE + "/turn",
        data=json.dumps({"player": p}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    results.append({"case": "缺少 action/request_id", "expect": 422, "actual": code,
                    "match": code == 422})
    ok &= code == 422

    pathlib.Path("evidence").mkdir(exist_ok=True)
    pathlib.Path("evidence/negative-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        print("%-18s 期望 %s 实际 %s  %s" % (r["case"], r["expect"], r["actual"],
                                             "OK" if r["match"] else "FAIL"))
    print("证据已写入 evidence/negative-results.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

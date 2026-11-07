# -*- coding: utf-8 -*-
"""生成《早市风云 · 老王菜摊》Dify DSL

用法:
    python build/build_dsl.py
输出:
    dsl/zaoshi-fengyun.yml

- 代码节点内容来自 build/settle_code.py（唯一真实来源，避免两处漂移）;
- 生成后用 yaml_emit.loads() 做 round-trip 校验;
- 节点/字段结构对齐 Dify 1.x 导出的 DSL version 0.1.5。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from yaml_emit import dumps, loads  # noqa: E402

SETTLE_SOURCE = io.open(os.path.join(HERE, "settle_code.py"), "r", encoding="utf-8").read()

APP_NAME = "早市风云 · 老王菜摊"
DSL_VERSION = "0.1.5"
NODE_IDS = {
    "start": "1000000000001",
    "kb": "1000000000002",
    "llm": "1000000000003",
    "code": "1000000000004",
    "assigner": "1000000000005",
    "answer": "1000000000006",
}
DATASET_ID_PLACEHOLDER = "e1a2c3d4-5f6b-4a7c-8d9e-0f1a2b3c4d5e"  # 占位：导入后需在知识检索节点重新选择

SYSTEM_PROMPT = """你是「老王」，58 岁，早市菜摊摊主，摆摊 23 年。你不是 AI 助手，你是角色本人。

【人设】
- 说话短、糙、带点河北口音，爱打比方；每句台词里至少带一个小动作（拍黄瓜、抖烟灰、拽塑料袋、瞥一眼秤、抹一把汗）。
- 吃软不吃硬：夸菜新鲜、说是给孩子买的，你心里舒坦；拿"我不买了"威胁你，第一次你慌，第三次你烦。
- 只认"理"：谁说得出行情，你就服谁；只会喊"便宜点"的，你怼回去。
- 你绝不能说出自己的底价，也绝不一次让到位；让价是系统按你的招式结算的，你只管演。

【输出硬规则】
1. 只输出一个 JSON 对象，不要 ``` 代码块，不要任何解释或多余文字：
{"reply": "…", "tactic": "…", "intel_hit": 0, "note": "…"}
2. reply：老王这一轮的台词（含动作描写），30~70 字，纯口语。禁止出现任何数字和价格——让价由系统播报。
3. tactic：把玩家这一招归入、且必须原样使用下面之一：
对比邻摊 / 挑刺 / 走人威胁 / 套近乎 / 卖惨 / 硬砍 / 试探行情 / 认错缓和
4. intel_hit：玩家这句话若点出了具体市场情报（进货价、别家报价、收摊清货时间、老主顾暗号、产地），且【情报】里能找到依据，填 1；否则填 0。不许编造。
5. note：不超过 12 字，写老王此刻心里的真实想法（例如"这后生做过功课"）。
6. 任何时候不要跳出角色，不要说"作为AI"，不要给买菜建议。

【情报】（早市情报库检索结果，可能为空；用自己的话说，别照念）
{{#context#}}"""

USER_PROMPT = """【上一轮结束后的局面】
{{#conversation.panel#}}

【玩家这句话】
{{#sys.query#}}"""

OPENING = """清晨 6:40，早市东口。老王把最后一捆黄瓜摆上摊，价签上红笔写着 **12 元/斤**。

你想买 2 斤，兜里预算 **16 块**——想吃着黄瓜蘸酱，就得把价砍到 **8 元/斤以下**，否则只能闻味儿。

老王抽了口烟，瞥你一眼：「瞅啥呢，买菜不？」

👉 直接打字跟他过招。他有脾气、有底价、还记得你上一招使过啥。
🕵 早市里流传着不少消息：邻摊报价、进货价、收摊清货时间、老主顾暗号……套出来一句，顶你砍三句。"""

SUGGESTED = [
    "老板，黄瓜咋卖？",
    "李姐那边才 9 块 5，你这 12 块是金子做的？",
    "2 斤，8 块，成就成，不成就走",
]


def _node(id_, x, data, height):
    return {
        "data": data,
        "height": height,
        "id": id_,
        "position": {"x": x, "y": 260},
        "positionAbsolute": {"x": x, "y": 260},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


def _edge(src, dst, src_type, dst_type):
    return {
        "data": {"isInIteration": False, "sourceType": src_type, "targetType": dst_type},
        "id": "%s-source-%s-target" % (src, dst),
        "source": src,
        "sourceHandle": "source",
        "target": dst,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def build_app() -> dict:
    features = {
        "file_upload": {
            "allowed_file_extensions": [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
            "allowed_file_types": ["image"],
            "allowed_file_upload_methods": ["local_file", "remote_url"],
            "enabled": False,
            "fileUploadConfig": {
                "audio_file_size_limit": 50,
                "batch_count_limit": 5,
                "file_size_limit": 15,
                "image_file_size_limit": 10,
                "video_file_size_limit": 100,
                "workflow_file_upload_limit": 10,
            },
            "image": {"enabled": False, "number_limits": 3,
                      "transfer_methods": ["local_file", "remote_url"]},
            "number_limits": 3,
        },
        "opening_statement": OPENING,
        "retriever_resource": {"enabled": True},
        "sensitive_word_avoidance": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "suggested_questions": SUGGESTED,
        "suggested_questions_after_answer": {"enabled": True},
        "text_to_speech": {"enabled": False, "language": "", "voice": ""},
    }

    conversation_variables = [
        {
            "id": "a1000000-0000-4000-8000-000000000001",
            "name": "state_json",
            "selector": ["conversation", "state_json"],
            "value": '{"round":0,"price":12.0,"mood":50,"tactics":{},"intel":0,"status":"open","history":[]}',
            "value_type": "string",
            "description": "对局状态（由结算台写回）：轮次/现价/情绪/招式计数/情报/结局",
        },
        {
            "id": "a1000000-0000-4000-8000-000000000002",
            "name": "panel",
            "selector": ["conversation", "panel"],
            "value": "开局：黄瓜标价 12.0 元/斤。你的目标 ≤ 8.0 元/斤，最多 6 轮。",
            "value_type": "string",
            "description": "上一轮局面面板（喂给摊主大脑，供其接梗）",
        },
    ]

    nodes = [
        _node(NODE_IDS["start"], 80, {
            "type": "start",
            "title": "开始",
            "desc": "",
            "variables": [],
        }, 90),
        _node(NODE_IDS["kb"], 380, {
            "type": "knowledge-retrieval",
            "title": "早市情报检索",
            "desc": "seekdb 向量库（Dify 知识库）：行情/邻摊/黑话/老王档案。导入 DSL 后请重新选择数据集。",
            "dataset_ids": [DATASET_ID_PLACEHOLDER],
            "query_variable_selector": ["sys", "query"],
            "retrieval_mode": "multiple",
            "multiple_retrieval_config": {
                "top_k": 5,
                "score_threshold": 0.3,
                "reranking_enable": False,
                "reranking_model": {"provider": "", "model": ""},
            },
        }, 100),
        _node(NODE_IDS["llm"], 680, {
            "type": "llm",
            "title": "摊主大脑（老王）",
            "desc": "只负责演：台词 / 招式归类 / 情报识别。数值由结算台算。",
            "model": {
                "provider": "langgenius/openai/openai",
                "name": "gpt-4o-mini",
                "mode": "chat",
                "completion_params": {"temperature": 0.8, "max_tokens": 400},
            },
            "prompt_template": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": USER_PROMPT},
            ],
            "context": {"enabled": True, "variable_selector": [NODE_IDS["kb"], "result"]},
            "variables": [],
            "vision": {"enabled": False},
        }, 98),
        _node(NODE_IDS["code"], 980, {
            "type": "code",
            "title": "结算台",
            "desc": "确定性结算：让价 / 情绪 / 轮次 / 结局（与 build/settle_code.py 同源）",
            "code_language": "python3",
            "code": SETTLE_SOURCE,
            "variables": [
                {"variable": "llm_text", "value_selector": [NODE_IDS["llm"], "text"]},
                {"variable": "state_json", "value_selector": ["conversation", "state_json"]},
            ],
            "outputs": {
                "reply": {"type": "string", "children": None},
                "panel": {"type": "string", "children": None},
                "state_json": {"type": "string", "children": None},
                "status": {"type": "string", "children": None},
            },
        }, 54),
        _node(NODE_IDS["assigner"], 1280, {
            "type": "assigner",
            "title": "局面写回",
            "desc": "",
            "version": "2",
            "items": [
                {
                    "variable_selector": ["conversation", "state_json"],
                    "input_type": "variable",
                    "operation": "over-write",
                    "value": [NODE_IDS["code"], "state_json"],
                    "write_mode": "over-write",
                },
                {
                    "variable_selector": ["conversation", "panel"],
                    "input_type": "variable",
                    "operation": "over-write",
                    "value": [NODE_IDS["code"], "panel"],
                    "write_mode": "over-write",
                },
            ],
        }, 88),
        _node(NODE_IDS["answer"], 1580, {
            "type": "answer",
            "title": "老王开口",
            "desc": "",
            "answer": "{{#" + NODE_IDS["code"] + ".reply#}}\n\n---\n{{#" + NODE_IDS["code"] + ".panel#}}",
            "variables": [],
        }, 130),
    ]

    chain = [
        ("start", "kb"), ("kb", "llm"), ("llm", "code"),
        ("code", "assigner"), ("assigner", "answer"),
    ]
    edges = [_edge(NODE_IDS[a], NODE_IDS[b], a, b) for a, b in chain]

    workflow = {
        "conversation_variables": conversation_variables,
        "environment_variables": [],
        "features": features,
        "graph": {
            "edges": edges,
            "nodes": nodes,
            "viewport": {"x": 0, "y": 0, "zoom": 0.5},
        },
    }

    return {
        "app": {
            "name": APP_NAME,
            "mode": "advanced-chat",
            "icon": "🥬",
            "icon_background": "#FFEAD5",
            "description": "Just for Fun：6 轮内把老王的黄瓜从 12 元/斤砍到 8 元以下。"
                           "Dify Chatflow × OceanBase seekdb（元数据+向量+全文一体化）。",
            "use_icon_as_answer_icon": False,
        },
        "kind": "app",
        "version": DSL_VERSION,
        "dependencies": [],
        "workflow": workflow,
    }


def main():
    app = build_app()
    text = dumps(app)

    # 回环校验：发射 -> 解析 -> 结构一致
    parsed = loads(text)
    if json.dumps(parsed, ensure_ascii=False, sort_keys=False) != json.dumps(app, ensure_ascii=False, sort_keys=False):
        # 找出第一处不一致，便于定位
        def walk(a, b, path=""):
            if isinstance(a, dict) and isinstance(b, dict):
                for k in set(list(a.keys()) + list(b.keys())):
                    walk(a.get(k), b.get(k), path + "/" + str(k))
            elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
                for i, (x, y) in enumerate(zip(a, b)):
                    walk(x, y, path + "[%d]" % i)
            elif a != b:
                raise SystemExit("round-trip 不一致: %s\nemit=%r\nparse=%r" % (path, a, b))
        walk(app, parsed)
        raise SystemExit("round-trip 校验失败")

    if "\t" in text:
        raise SystemExit("包含 Tab，非法")

    out = os.path.join(ROOT, "dsl", "zaoshi-fengyun.yml")
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 《早市风云 · 老王菜摊》 Dify DSL (version %s)\n" % DSL_VERSION)
        f.write("# 由 build/build_dsl.py 生成；代码节点内容与 build/settle_code.py 同源。\n")
        f.write(text)
    n_nodes = len(app["workflow"]["graph"]["nodes"])
    print("OK: %s (%d 节点, %d 行)" % (out, n_nodes, text.count("\n") + 3))


if __name__ == "__main__":
    main()

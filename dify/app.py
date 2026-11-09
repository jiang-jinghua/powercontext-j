# -*- coding: utf-8 -*-
"""早市风云 · 老王菜摊 —— HTTP 服务（Dify 工作流的下游节点）

职责：
1. 把一局砍价游戏的"局面"持久化在 seekdb（MySQL 协议）里，玩家关掉页面再回来仍在原局；
2. 用 (player, request_id) 做幂等，Dify 网络重试不会重复推进轮次；
3. 所有数值结算委托 game.py（确定性、可复现），本文件只管 IO 与事务。

接口：
  POST /turn            {player, action, request_id} -> 一回合结果
  GET  /state/{player}  读取持久化局面（验收用）
  GET  /healthz         存活与数据库连通性
鉴权：Authorization: Bearer $GAME_TOKEN
"""
import json
import os
import time

import pymysql
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import game

def _load_dotenv():
    """本地直接跑时读同目录 .env；compose 里由 environment 注入，二者等价。"""
    import pathlib

    p = pathlib.Path(__file__).with_name(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

app = FastAPI(title="早市风云 · 老王菜摊", version="1.0.0")

DB_NAME = os.getenv("DB_NAME", "zaoshi")
_SCHEMA_READY = False

INTEL_SEED = [
    ("进货价", "这批黄瓜凌晨四点从批发市场拉的，进价七块二一斤"),
    ("邻摊价", "东头老李今天卖八块五，他的货是泡过水的"),
    ("收摊规矩", "过了九点半我就要收摊，剩下的菜宁可贱卖"),
]


def db_config():
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "2881")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 10,
    }


class DatabaseUnavailable(RuntimeError):
    """seekdb 未就绪或连接失败。"""


def connect(database=None, retries=0, delay=3.0):
    """连接 seekdb；容器刚起来时数据库可能还在 bootstrap，按 retries 重试。"""
    last = None
    for i in range(max(0, retries) + 1):
        try:
            return pymysql.connect(database=database, **db_config())
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < retries:
                time.sleep(delay)
    raise DatabaseUnavailable("seekdb 暂不可用：%s" % last)


def ensure_schema():
    """建库建表 + 灌入行情数据，可重复执行。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    c = connect(retries=40, delay=3.0)
    try:
        with c.cursor() as q:
            q.execute("CREATE DATABASE IF NOT EXISTS `%s`" % DB_NAME)
        c.commit()
    finally:
        c.close()

    c = connect(database=DB_NAME, retries=5, delay=2.0)
    try:
        with c.cursor() as q:
            q.execute(
                "CREATE TABLE IF NOT EXISTS zaoshi_state ("
                "player VARCHAR(64) PRIMARY KEY, round_no INT NOT NULL, price DOUBLE NOT NULL, "
                "mood INT NOT NULL, intel INT NOT NULL, tactics TEXT NOT NULL, status VARCHAR(16) NOT NULL)"
            )
            q.execute(
                "CREATE TABLE IF NOT EXISTS zaoshi_events ("
                "player VARCHAR(64) NOT NULL, request_id VARCHAR(64) NOT NULL, "
                "response MEDIUMTEXT NOT NULL, PRIMARY KEY (player, request_id))"
            )
            q.execute(
                "CREATE TABLE IF NOT EXISTS zaoshi_intel ("
                "id INT PRIMARY KEY, topic VARCHAR(64) NOT NULL, hint VARCHAR(255) NOT NULL)"
            )
            for i, (topic, hint) in enumerate(INTEL_SEED, start=1):
                q.execute(
                    "INSERT IGNORE INTO zaoshi_intel (id, topic, hint) VALUES (%s, %s, %s)",
                    (i, topic, hint),
                )
        c.commit()
    finally:
        c.close()
    _SCHEMA_READY = True


class Move(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=64)


def _check_token(authorization: str):
    token = os.getenv("GAME_TOKEN", "")
    if not token or authorization != "Bearer " + token:
        raise HTTPException(401, "invalid token")


@app.exception_handler(DatabaseUnavailable)
def _db_down(_request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/healthz")
def healthz():
    try:
        ensure_schema()
        c = connect(database=DB_NAME, retries=1)
        try:
            with c.cursor() as q:
                q.execute("SELECT 1")
                q.fetchone()
        finally:
            c.close()
        return {"ok": True, "database": DB_NAME, "storage": "seekdb"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "seekdb 不可用：%s" % exc)


@app.get("/state/{player}")
def state(player: str, authorization: str = Header(default="")):
    _check_token(authorization)
    ensure_schema()
    c = connect(database=DB_NAME)
    try:
        with c.cursor() as q:
            q.execute(
                "SELECT round_no, price, mood, intel, tactics, status "
                "FROM zaoshi_state WHERE player=%s",
                (player,),
            )
            row = q.fetchone()
    finally:
        c.close()
    if not row:
        raise HTTPException(404, "该玩家还没有开局")
    return {
        "player": player,
        "round": int(row[0]),
        "price": float(row[1]),
        "mood": int(row[2]),
        "intel": int(row[3]),
        "tactics": json.loads(row[4] or "{}"),
        "status": row[5],
        "storage": "seekdb",
    }


@app.post("/turn")
def turn(m: Move, authorization: str = Header(default="")):
    _check_token(authorization)
    tactic = game.parse_action(m.action)
    if not tactic:
        raise HTTPException(
            422, "无法识别的招式，可选：" + " / ".join(game.CHOICES)
        )

    ensure_schema()
    c = connect(database=DB_NAME)
    try:
        with c.cursor() as q:
            q.execute(
                "INSERT IGNORE INTO zaoshi_state "
                "(player, round_no, price, mood, intel, tactics, status) "
                "VALUES (%s, 0, %s, 50, 0, '{}', 'open')",
                (m.player, game.START_PRICE),
            )
            q.execute(
                "SELECT round_no, price, mood, intel, tactics, status "
                "FROM zaoshi_state WHERE player=%s FOR UPDATE",
                (m.player,),
            )
            rnd, price, mood, intel, tactics, status = q.fetchone()

            # 幂等：同一个 request_id 直接回放上次结果，不推进轮次
            q.execute(
                "SELECT response FROM zaoshi_events WHERE player=%s AND request_id=%s",
                (m.player, m.request_id),
            )
            cached = q.fetchone()
            if cached:
                c.commit()
                return json.loads(cached[0])

            st = {
                "round": int(rnd),
                "price": float(price),
                "mood": int(mood),
                "intel": int(intel),
                "tactics": json.loads(tactics or "{}"),
                "status": status,
            }
            restarted = st["status"] != "open"
            if restarted:
                st = game.new_state()

            hint = ""
            if tactic == "试探行情" and not st["intel"]:
                q.execute("SELECT hint FROM zaoshi_intel ORDER BY id LIMIT 1")
                got = q.fetchone()
                hint = got[0] if got else ""

            res = game.settle(st, tactic, hint)
            st = res["state"]

            out = {
                "player": m.player,
                "action": m.action,
                "tactic": tactic,
                "round": st["round"],
                "price": st["price"],
                "mood": st["mood"],
                "intel": st["intel"],
                "status": st["status"],
                "reply": res["reply"],
                "choices": game.CHOICES,
                "restarted": restarted,
                "storage": "seekdb",
            }

            q.execute(
                "UPDATE zaoshi_state SET round_no=%s, price=%s, mood=%s, intel=%s, "
                "tactics=%s, status=%s WHERE player=%s",
                (st["round"], st["price"], st["mood"], st["intel"],
                 json.dumps(st["tactics"], ensure_ascii=False), st["status"], m.player),
            )
            q.execute(
                "INSERT INTO zaoshi_events (player, request_id, response) VALUES (%s, %s, %s)",
                (m.player, m.request_id, json.dumps(out, ensure_ascii=False)),
            )
        c.commit()
    finally:
        c.close()
    return out

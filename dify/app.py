import os,json,hashlib
import pymysql
from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel,Field
app=FastAPI(title="回声旅店")
def db():
    return pymysql.connect(host=os.getenv("DB_HOST","127.0.0.1"),port=12881,user="root",password=os.environ["DB_PASSWORD"],database="echo_inn",charset="utf8mb4",autocommit=False)
def init():
    with db() as c:
        with c.cursor() as q:
            q.execute("CREATE TABLE IF NOT EXISTS echo_players (player VARCHAR(64) PRIMARY KEY, trust INT NOT NULL, turn INT NOT NULL)")
            q.execute("CREATE TABLE IF NOT EXISTS echo_events (player VARCHAR(64), request_id VARCHAR(64), result TEXT NOT NULL, PRIMARY KEY(player,request_id))")
        c.commit()
class Move(BaseModel):
    player: str=Field(min_length=1,max_length=64)
    action: str=Field(min_length=1,max_length=200)
    request_id: str=Field(min_length=1,max_length=64)
@app.post("/turn")
def turn(m:Move,authorization:str=Header(default="")):
    if authorization != "Bearer "+os.environ["GAME_TOKEN"]: raise HTTPException(401,"invalid token")
    if m.action not in ["开始","归还钥匙","偷看账本","询问秘密"]: raise HTTPException(422,"请选择：开始 / 归还钥匙 / 偷看账本 / 询问秘密")
    init()
    with db() as c:
        with c.cursor() as q:
            q.execute("INSERT IGNORE INTO echo_players VALUES (%s,0,0)",(m.player,))
            q.execute("SELECT trust,turn FROM echo_players WHERE player=%s FOR UPDATE",(m.player,))
            trust,n=q.fetchone()
            q.execute("SELECT result FROM echo_events WHERE player=%s AND request_id=%s",(m.player,m.request_id))
            old=q.fetchone()
            if old: c.commit(); return json.loads(old[0])
            if m.action=="归还钥匙": trust+=1; story="店主收下钥匙：我会记住你的善意。"
            elif m.action=="偷看账本": trust-=1; story="你发现账本上的雨夜记录，店主却看到了你的动作。"
            elif m.action=="询问秘密": story="店主递来地下室钥匙：雨夜失踪的人，是未来的我。" if trust>0 else "店主合上账本：我们还不够熟。先帮我找回钥匙吧。"
            else: story="欢迎来到回声旅店。桌上有一把遗失的钥匙，柜台后藏着一本账本。" if n==0 else "你又回来了。店主仍记得你，信任度："+str(trust)
            n+=1
            out={"story":story,"trust":trust,"turn":n,"choices":["归还钥匙","偷看账本","询问秘密"],"storage":"seekdb"}
            q.execute("UPDATE echo_players SET trust=%s,turn=%s WHERE player=%s",(trust,n,m.player))
            q.execute("INSERT INTO echo_events VALUES (%s,%s,%s)",(m.player,m.request_id,json.dumps(out,ensure_ascii=False)))
        c.commit()
    return out

import json,urllib.request,os,uuid,pathlib
base='http://127.0.0.1:18080/turn'
env=dict(line.split('=',1) for line in pathlib.Path('.env').read_text().splitlines() if '=' in line)
p='verify-'+uuid.uuid4().hex[:12]
rows=[]
for i,(a,t) in enumerate([('开始',0),('询问秘密',0),('归还钥匙',1),('询问秘密',1),('开始',1)]):
    body={'player':p,'action':a,'request_id':str(i)}
    req=urllib.request.Request(base,json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+env['GAME_TOKEN']})
    r=json.load(urllib.request.urlopen(req)); assert r['trust']==t and r['turn']==i+1,r
    rows.append({'input':body,'output':r})
req=urllib.request.Request(base,json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+env['GAME_TOKEN']})
assert json.load(urllib.request.urlopen(req))==r
pathlib.Path('evidence').mkdir(exist_ok=True)
pathlib.Path('evidence/backend-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print('5 turns and idempotency passed; evidence saved')

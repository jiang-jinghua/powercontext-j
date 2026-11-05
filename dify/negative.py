import json,pathlib,urllib.request,urllib.error
p=pathlib.Path('/home/jjh/echo-inn'); env=dict(x.split('=',1) for x in (p/'.env').read_text().splitlines())
results=[]
for action,token,expected in [('开始','bad',401),('非法动作',env['GAME_TOKEN'],422)]:
 req=urllib.request.Request('http://127.0.0.1:18080/turn',json.dumps({'player':'negative-case','action':action,'request_id':'1'}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
 try: urllib.request.urlopen(req);raise AssertionError('unexpected success')
 except urllib.error.HTTPError as e: assert e.code==expected;results.append({'case':action,'status':e.code})
(p/'evidence/negative-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print('authorization and invalid input checks passed')

import os,time,requests
from dotenv import dotenv_values
K=dotenv_values('/Users/zhangzeyuan/Projects/quant-ETF/.env')['FUYAO_API_KEY']
B='https://fuyao.aicubes.cn'
_last=[0]
def get(path,params=None,stream=False,timeout=60):
    for i in range(4):
        w=0.5-(time.time()-_last[0])
        if w>0: time.sleep(w)
        _last[0]=time.time()
        try:
            r=requests.get(B+path,params=params,headers={'X-api-key':K},timeout=timeout,stream=stream)
        except Exception as e:
            err=str(e).replace(K,'***'); 
            if i==3: return None
            continue
        if r.status_code in (429,500,502,503): continue
        return r
    return r

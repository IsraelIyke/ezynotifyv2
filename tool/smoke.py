"""Read-only/service smoke checks. Does not send messages or create monitors."""
import json
import urllib.request
import urllib.error
from control import ROOT,read_env


def request(url,body,headers=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urllib.request.urlopen(req,timeout=45) as response:
            raw=response.read();return response.status,json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:return error.code,None


def main():
    v=read_env(ROOT/'.secrets'/'deployment.env');base=v['SUPABASE_URL']+'/functions/v1/'
    for slug in ['ezynotify-telegram','ezynotify-dispatch','ezynotify-ai']:
        status,_=request(base+slug,{})
        if status!=401:raise RuntimeError('Unauthenticated endpoint check failed for '+slug)
        print(slug+': rejects unauthenticated requests')
    status,value=request(base+'ezynotify-ai',{'action':'summarize','changes':[{'removed':'The ticket costs 10 dollars.','added':'The ticket costs 12 dollars.'}]},{'x-ai-secret':v['MONITOR_AI_SECRET']})
    if status!=200 or value.get('source')!='groq' or not value.get('summary'):raise RuntimeError('Live Groq summarization failed')
    print('Live Groq summary: passed; response not printed')
    status,value=request(base+'ezynotify-ai',{'action':'classify','text':'Verify you are human. Complete the CAPTCHA to access this page.'},{'x-ai-secret':v['MONITOR_AI_SECRET']})
    if status!=200:raise RuntimeError('Live Groq classification failed')
    print('Live Groq classification:',value.get('status'))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,RuntimeError) else 'Smoke check failed ('+type(error).__name__+')')
        raise SystemExit(1)

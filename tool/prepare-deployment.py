"""Prepare ignored deployment secrets from the user-authorized local sources.

Prints only setting names and model IDs. Never prints keys or bot tokens.
"""
import argparse
import json
import re
import secrets
from pathlib import Path
from urllib.parse import unquote
import urllib.request
from control import ROOT,read_env,management


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--vision-model',default='')
    parser.add_argument('--bot-env',help='Optional file containing BOT_TOKEN or EZYNOTIFY_BOT_TOKEN')
    args=parser.parse_args()
    private=ROOT/'.secrets';private.mkdir(exist_ok=True)
    destination=private/'deployment.env'
    values=read_env(destination)
    groq=read_env(ROOT.parent/'estimateQBackend'/'.env').get('GROQ_API_KEY')
    if not groq:raise RuntimeError('Groq key missing from authorized source')
    req=urllib.request.Request('https://api.groq.com/openai/v1/models',headers={'Authorization':'Bearer '+groq,'User-Agent':'EzyNotify/3.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:models={m['id'] for m in json.load(response)['data']}
    if args.model not in models:raise RuntimeError('The requested summary model is unavailable. Available model IDs: '+', '.join(sorted(models)))
    if args.vision_model and args.vision_model not in models:raise RuntimeError('The requested vision model is unavailable')
    keys=management(f'projects/{args.project}/api-keys')
    service=next((k.get('api_key') for k in keys if k.get('name')=='service_role'),None)
    if not service:raise RuntimeError('Service-role key unavailable')
    cfg=(ROOT.parent/'ezynotify'/'.git'/'config').read_text()
    credential=unquote(re.search(r'https://([^/@\s]+)@github\.com',cfg).group(1))
    pat=credential.split(':',1)[-1]
    values.update({
      'PROJECT_REF':args.project,'SUPABASE_URL':f'https://{args.project}.supabase.co','SUPABASE_KEY':service,
      'GROQ_API_KEY':groq,'GROQ_MODEL':args.model,'GROQ_VISION_MODEL':args.vision_model,
      'GITHUB_REPOSITORY':'IsraelIyke/ezynotifyv2','GITHUB_WORKFLOW':'ezynotify.yml','GITHUB_REF':'main','GITHUB_DISPATCH_TOKEN':pat,
    })
    values.setdefault('GROQ_REASONING_EFFORT','low')
    values.setdefault('GROQ_VISION_REASONING_EFFORT','none')
    for name in ['TELEGRAM_WEBHOOK_SECRET','DISPATCH_SECRET','MONITOR_AI_SECRET']:
        values.setdefault(name,secrets.token_urlsafe(32))
    values['MONITOR_AI_ENDPOINT']=f'https://{args.project}.supabase.co/functions/v1/ezynotify-ai'
    if args.bot_env:
        bot=read_env(args.bot_env)
        token=bot.get('EZYNOTIFY_BOT_TOKEN') or bot.get('BOT_TOKEN')
        if not token:raise RuntimeError('No EzyNotify bot token in supplied file')
        values['BOT_TOKEN']=token
    destination.write_text('\n'.join(f'{k}={v}' for k,v in values.items())+'\n',encoding='utf-8')
    print('Prepared ignored deployment settings:',', '.join(sorted(values)))
    print('Summary model:',args.model)
    print('Vision model:',args.vision_model or 'disabled')
    print('Main bot token:','available' if values.get('BOT_TOKEN') else 'still required')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Unable to prepare deployment settings:',str(error) if isinstance(error,RuntimeError) else type(error).__name__)
        if hasattr(error,'code'):print('HTTP status:',error.code)
        raise SystemExit(1)

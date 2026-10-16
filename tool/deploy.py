"""Deploy reviewed artifacts. All secrets are read from ignored local storage."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.request
from control import ROOT,read_env,management,github


def settings():
    values=read_env(ROOT/'.secrets'/'deployment.env')
    if not values.get('PROJECT_REF'):raise RuntimeError('Run prepare-deployment.py first')
    return values


def set_secrets(values):
    names=['BOT_TOKEN','TELEGRAM_WEBHOOK_SECRET','DISPATCH_SECRET','MONITOR_AI_SECRET','GROQ_API_KEY','GROQ_MODEL','GROQ_VISION_MODEL','GROQ_REASONING_EFFORT','GROQ_VISION_REASONING_EFFORT','GITHUB_REPOSITORY','GITHUB_WORKFLOW','GITHUB_REF','GITHUB_DISPATCH_TOKEN']
    secrets=[{'name':name,'value':values[name]} for name in names if name in values]
    management(f'projects/{values["PROJECT_REF"]}/secrets',secrets,method='POST')
    print('Updated Supabase secrets:',', '.join(s['name'] for s in secrets))


def set_github_secrets(values):
    from nacl.public import PublicKey,SealedBox
    repository=values['GITHUB_REPOSITORY']
    key=github(f'repos/{repository}/actions/secrets/public-key')
    box=SealedBox(PublicKey(base64.b64decode(key['key'])))
    for name in ['SUPABASE_URL','SUPABASE_KEY','MONITOR_AI_ENDPOINT','MONITOR_AI_SECRET','BOT_TOKEN']:
        if not values.get(name):continue
        encrypted=base64.b64encode(box.encrypt(values[name].encode())).decode()
        github(f'repos/{repository}/actions/secrets/{name}',{'encrypted_value':encrypted,'key_id':key['key_id']},method='PUT')
        print('Updated GitHub secret:',name)


def deploy_functions(values):
    env=os.environ.copy()
    env['SUPABASE_ACCESS_TOKEN']=env.get('SUPABASE_ACCESS_TOKEN') or read_env(ROOT.parent/'guru-bot'/'.env')['SUPABASE_ACCESS_TOKEN']
    for slug in ['ezynotify-telegram','ezynotify-dispatch','ezynotify-ai','ezynotify-notifications']:
        # CLI reads only the access token from the subprocess environment.
        result=subprocess.run(['supabase.cmd','functions','deploy',slug,'--project-ref',values['PROJECT_REF'],'--use-api','--no-verify-jwt'],cwd=ROOT,env=env,capture_output=True,text=True)
        if result.returncode:raise RuntimeError(f'Function deployment failed for {slug}; no raw CLI output printed')
        print('Deployed function:',slug)


def telegram_call(values,method,body):
    if not values.get('BOT_TOKEN'):raise RuntimeError('Main EzyNotify bot token is required')
    req=urllib.request.Request(f'https://api.telegram.org/bot{values["BOT_TOKEN"]}/{method}',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:data=json.load(response)
    if not data.get('ok'):raise RuntimeError(f'Telegram {method} failed')
    return data['result']


def configure_bot(values):
    # No profile-photo API exists in this script. Preserve existing photo.
    me=telegram_call(values,'getMe',{})
    if me.get('username','').lower()!='ezynotify_bot':raise RuntimeError('Refusing to reconfigure a different Telegram bot')
    telegram_call(values,'setMyDescription',{'description':'🔔 Your websites. Your updates. Less checking.\n\nTrack public pages for changes or keywords, get concise AI summaries, and manage everything with simple buttons.\n\n🧠 Clear change summaries\n🔎 Keyword alerts\n🛡 Honest access-block notices\n⏱ Flexible check schedules\n\nTap Start to create your first monitor.'})
    telegram_call(values,'setMyShortDescription',{'short_description':'🔔 Website changes & keyword alerts, with clear AI summaries. Add a page and let EzyNotify keep watch.'})
    telegram_call(values,'setMyCommands',{'commands':[{'command':'start','description':'🔔 Open your dashboard'},{'command':'new','description':'✨ Create a website monitor'},{'command':'monitors','description':'📋 View and manage your monitors'},{'command':'timezone','description':'🌍 Set your local timezone'},{'command':'help','description':'💡 How monitoring works'},{'command':'cancel','description':'✖️ Discard the current draft'}]})
    commands=telegram_call(values,'getMyCommands',{})
    commands=[c for c in commands if c['command']!='privacy']
    commands.append({'command':'privacy','description':'Privacy and responsible use'})
    telegram_call(values,'setMyCommands',{'commands':commands})
    telegram_call(values,'setChatMenuButton',{'menu_button':{'type':'commands'}})
    telegram_call(values,'setWebhook',{'url':f'https://{values["PROJECT_REF"]}.supabase.co/functions/v1/ezynotify-telegram','secret_token':values['TELEGRAM_WEBHOOK_SECRET'],'allowed_updates':['message','callback_query'],'max_connections':1,'drop_pending_updates':False})
    info=telegram_call(values,'getWebhookInfo',{})
    print('Configured @'+me['username']+' webhook:',info.get('url'))
    print('Profile image untouched. Pending updates:',info.get('pending_update_count'))


def schedule(values):
    project=values['PROJECT_REF']
    # Secret literals are transmitted only in the TLS API body, never stored in
    # a committed SQL file or printed in command text.
    literal=lambda s:"'"+s.replace("'","''")+"'"
    for name,value in [('ezynotify_project_url',values['SUPABASE_URL']),('ezynotify_dispatch_secret',values['DISPATCH_SECRET'])]:
        sql=f"do $$ declare existing uuid; begin select id into existing from vault.secrets where name={literal(name)}; if existing is null then perform vault.create_secret({literal(value)},{literal(name)}); else perform vault.update_secret(existing,{literal(value)},{literal(name)}); end if; end $$;"
        management(f'projects/{project}/database/query',{'query':sql})
    sql=(ROOT/'supabase'/'schedule.sql').read_text()
    management(f'projects/{project}/database/query',{'query':sql})
    print('Installed EzyNotify scheduler and retention jobs')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['secrets','functions','github-secrets','bot','schedule','status','verify-bot'])
    args=parser.parse_args();values=settings()
    if args.command=='secrets':set_secrets(values)
    elif args.command=='github-secrets':set_github_secrets(values)
    elif args.command=='functions':deploy_functions(values)
    elif args.command=='bot':configure_bot(values)
    elif args.command=='schedule':schedule(values)
    elif args.command=='status':
        info=telegram_call(values,'getWebhookInfo',{});print(json.dumps({k:info.get(k) for k in ['url','pending_update_count','last_error_message']},indent=2))
    elif args.command=='verify-bot':
        me=telegram_call(values,'getMe',{});info=telegram_call(values,'getWebhookInfo',{})
        if me.get('username','').lower()!='ezynotify_bot':raise RuntimeError('Wrong bot token')
        from urllib.parse import urlsplit
        url=urlsplit(info.get('url',''))
        print('Verified bot:',me.get('username'))
        print('Current webhook host:',url.netloc)
        print('Pending updates:',info.get('pending_update_count'))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Raw network exceptions can contain token-bearing Telegram URLs.
        print('Deployment step failed ('+type(error).__name__+'). No secret-bearing response was printed.')
        raise SystemExit(1)

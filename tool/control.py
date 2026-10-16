"""Private deployment helpers. Never print secret values or raw API errors."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
import urllib.error
import base64
import re

ROOT = Path(__file__).resolve().parents[1]


def read_env(path):
    result = {}
    if not Path(path).exists():
        return result
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        result[key.strip()] = value.strip().strip('\"').strip("'")
    return result


def management(path, body=None, method=None):
    token = os.environ.get('SUPABASE_ACCESS_TOKEN') or read_env(ROOT.parent/'guru-bot'/'.env').get('SUPABASE_ACCESS_TOKEN')
    if not token:
        raise RuntimeError('Missing Supabase management access token')
    request = urllib.request.Request('https://api.supabase.com/v1/'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data=response.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Management request failed ({error.code})') from None


def github(path,body=None,method=None):
    config=(ROOT.parent/'ezynotify'/'.git'/'config').read_text()
    match=re.search(r'https://([^/@\s]+)@github\.com',config)
    if not match:raise RuntimeError('Missing stored GitHub credential')
    from urllib.parse import unquote
    credential=unquote(match.group(1))
    if ':' not in credential:credential='x-access-token:'+credential
    request=urllib.request.Request('https://api.github.com/'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Basic '+base64.b64encode(credential.encode()).decode(),'Accept':'application/vnd.github+json','Content-Type':'application/json','X-GitHub-Api-Version':'2022-11-28'},method=method)
    try:
        with urllib.request.urlopen(request,timeout=40) as response:
            data=response.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:raise RuntimeError(f'GitHub request failed ({error.code})') from None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['projects','inspect','query','github-info','resume-worker'])
    parser.add_argument('--project')
    parser.add_argument('--sql-file')
    args=parser.parse_args()
    if args.command=='resume-worker':
        github('repos/IsraelIyke/ezynotifyv2/actions/workflows/ezynotify.yml/enable',method='PUT')
        print('Existing checker re-enabled. New scheduler has not been installed.')
        return
    if args.command=='github-info':
        for repo in ['ezynotifyv2','ezynotify-bot']:
            print(repo)
            for suffix in ['actions/secrets','deployments?per_page=3']:
                try:
                    value=github(f'repos/IsraelIyke/{repo}/{suffix}')
                    if isinstance(value,dict):print('secret_names:',[s['name'] for s in value.get('secrets',[])])
                    else:print('deployments:',[{k:d.get(k) for k in ['id','environment','created_at']} for d in value])
                except RuntimeError as error:print(str(error))
        return
    if args.command=='projects':
        projects=management('projects')
        print(json.dumps([{k:p.get(k) for k in ['id','name','status','region']} for p in projects],indent=2))
    elif args.command=='inspect':
        if not args.project:parser.error('--project required')
        functions=management(f'projects/{args.project}/functions')
        secrets=management(f'projects/{args.project}/secrets')
        print(json.dumps({'functions':[{k:f.get(k) for k in ['slug','status','verify_jwt']} for f in functions], 'secret_names':[s['name'] for s in secrets]},indent=2))
    elif args.command=='query':
        if not args.project or not args.sql_file:parser.error('--project and --sql-file required')
        # Only use for explicitly non-secret metadata queries or reviewed migrations.
        result=management(f'projects/{args.project}/database/query',{'query':Path(args.sql_file).read_text(encoding='utf-8-sig')})
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error))
        raise SystemExit(1)

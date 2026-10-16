"""Publish the reviewed implementation without putting credentials in Git URLs."""
import base64
import os
import re
import subprocess
import argparse
from control import ROOT,read_env


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--push',action='store_true',help='Requires explicit authorization to publish to main')
    args=parser.parse_args()
    values=read_env(ROOT/'.secrets'/'deployment.env')
    env=os.environ.copy()
    credential='x-access-token:'+values['GITHUB_DISPATCH_TOKEN']
    env.update(GIT_CONFIG_COUNT='2',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(ROOT).replace('\\','/'),
        GIT_CONFIG_KEY_1='http.https://github.com/.extraheader',GIT_CONFIG_VALUE_1='AUTHORIZATION: basic '+base64.b64encode(credential.encode()).decode(),GIT_TERMINAL_PROMPT='0')
    def git(*args):
        result=subprocess.run(['git',*args],cwd=ROOT,env=env,capture_output=True)
        if result.returncode:raise RuntimeError('Git operation failed: '+args[0])
        return result.stdout
    if args.push:
        git('fetch','origin','main')
        git('merge-base','--is-ancestor','origin/main','HEAD')
        if git('status','--porcelain').strip():raise RuntimeError('Uncommitted changes; review before publishing')
        git('push','origin','HEAD:main')
        print('Published commit:',git('rev-parse','--short','HEAD').decode().strip())
        return
    if git('rev-parse','HEAD').strip()!=git('rev-parse','origin/main').strip():raise RuntimeError('Remote changed; review before publishing')
    git('add','--','.github','README.md','ezynotify.py','requirements.txt','.env.example','.gitignore','assets','monitor','package.json','package-lock.json','supabase','test','tool')
    files=git('diff','--cached','--name-only','--diff-filter=ACM').decode().splitlines()
    private_values=[v for k,v in values.items() if any(word in k for word in ['TOKEN','SECRET','KEY']) and len(v)>12]
    for path in files:
        if path.startswith(('.secrets/','.test-deps/')):raise RuntimeError('Private path staged')
        content=git('show',':'+path)
        if path.endswith(('.gif','.png','.jpg')):continue
        text=content.decode('utf-8','replace')
        if any(value in text for value in private_values):raise RuntimeError('A credential was found in staged content')
        if re.search(r'(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gsk_[A-Za-z0-9]{20,})',text):raise RuntimeError('A credential-like value was found in staged content')
    git('diff','--cached','--check')
    git('commit','-m','Unify EzyNotify on Supabase with Groq summaries and resilient browser monitoring')
    print('Prepared local commit:',git('rev-parse','--short','HEAD').decode().strip())
    print('Files checked for credentials:',len(files))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,RuntimeError) else 'Publish failed ('+type(error).__name__+')')
        raise SystemExit(1)

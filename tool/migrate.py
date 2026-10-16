"""Back up the existing monitor rows privately, then apply the reviewed upgrade."""
import json
from datetime import datetime,timezone
from control import ROOT,read_env,management,github


def main():
    values=read_env(ROOT/'.secrets'/'deployment.env');project=values['PROJECT_REF'];repo=values['GITHUB_REPOSITORY']
    workflow=f'repos/{repo}/actions/workflows/ezynotify.yml'
    active=github(workflow+'/runs?per_page=10')
    if any(run['status'] in ('queued','in_progress','waiting','pending') for run in active.get('workflow_runs',[])):
        raise RuntimeError('An old worker is active; wait for it to finish before migrating')
    github(workflow+'/disable',method='PUT')
    try:
        rows=management(f'projects/{project}/database/query',{'query':'select * from public.ezynotify order by id'})
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup=ROOT/'.secrets'/f'ezynotify-before-{stamp}.json'
        backup.write_text(json.dumps(rows),encoding='utf-8')
        print('Private backup created. Monitor count:',len(rows))
        migration=(ROOT/'supabase'/'migrations'/'202610160001_monitoring_v3.sql').read_text()
        management(f'projects/{project}/database/query',{'query':migration})
        result=management(f'projects/{project}/database/query',{'query':"select count(*)::integer as monitors, count(*) filter(where paused)::integer as paused from public.ezynotify"})
        if result[0]['monitors']!=len(rows):raise RuntimeError('Monitor count changed during migration; inspect before cutover')
        print('Database upgrade complete; all original monitor rows retained.')
        print('Old worker schedule is disabled until the new workflow is published.')
    except Exception:
        github(workflow+'/enable',method='PUT')
        raise


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,RuntimeError) else 'Migration failed ('+type(error).__name__+')')
        raise SystemExit(1)

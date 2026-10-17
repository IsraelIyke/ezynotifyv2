"""Read-only production fetch check; never changes a monitor or sends alerts."""
import asyncio
import json
import os
from pathlib import Path
import traceback
from urllib.parse import urlencode
import aiohttp
from .fetcher import Fetcher
from .store import Store


async def run():
    monitor_id=os.environ.get('MONITOR_ID','')
    if not monitor_id:raise ValueError('A monitor ID is required')
    async with aiohttp.ClientSession() as session:
        rows=await Store(session).request('ezynotify?'+urlencode({'id':'eq.'+monitor_id,'select':'id,url,fetch_config'}))
    if not rows:raise ValueError('Monitor not found')
    row=rows[0];downloads=[];failures=[]
    class DiagnosticFetcher(Fetcher):
        async def request(self,*args,**kwargs):
            try:result=await super().request(*args,**kwargs)
            except Exception as error:
                failures.append({'type':type(error).__name__,'frames':[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-5:]]})
                raise
            downloads.append(len(result[2]))
            return result
    async with DiagnosticFetcher() as fetcher:
        result=await fetcher.fetch(row['url'],{**(row.get('fetch_config') or {}),'screenshot':False})
    print(json.dumps({'monitor_id':monitor_id,'status':result.status,'reason':result.reason,
        'readable_characters':len(result.text),'largest_response_bytes':max(downloads,default=0),
        'total_response_bytes':sum(downloads),'requests':len(downloads),'request_failures':failures[:3]},ensure_ascii=True))
    return 0 if result.status=='ok' else 1


if __name__=='__main__':
    try:raise SystemExit(asyncio.run(run()))
    except Exception as error:
        print('Read-only fetch diagnosis failed ('+type(error).__name__+').')
        raise SystemExit(1)

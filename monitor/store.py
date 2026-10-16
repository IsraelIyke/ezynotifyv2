import asyncio
import os
from urllib.parse import urlencode
import aiohttp


class Store:
    def __init__(self,session,url=None,key=None):
        self.session=session
        self.url=(url or os.environ['SUPABASE_URL']).rstrip('/')
        self.key=key or os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']

    async def request(self,path,method='GET',body=None,prefer=None):
        headers={'apikey':self.key,'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}
        if prefer:headers['Prefer']=prefer
        async with self.session.request(method,self.url+'/rest/v1/'+path,headers=headers,json=body,timeout=aiohttp.ClientTimeout(total=25)) as response:
            if not response.ok:raise RuntimeError(f'Database request failed ({response.status})')
            data=await response.text()
            import json
            return json.loads(data) if data else None

    async def rpc(self,name,body):
        return await self.request('rpc/'+name,'POST',body)

    async def cache(self,key):
        rows=await self.request('fetch_cache?'+urlencode({'cache_key':'eq.'+key,'select':'result,fetched_at'}))
        return rows[0] if rows else None

    async def set_cache(self,key,result):
        from .models import utcnow
        await self.request('fetch_cache?on_conflict=cache_key','POST',{'cache_key':key,'result':result.cached(),'fetched_at':utcnow().isoformat()},'resolution=merge-duplicates')

    async def domain_cooldown(self,domain):
        from .models import utcnow
        from datetime import datetime
        if not domain:return 0
        rows=await self.request('runtime_locks?'+urlencode({'name':'eq.domain:'+domain,'select':'expires_at'}))
        return max(0,int((datetime.fromisoformat(rows[0]['expires_at'].replace('Z','+00:00'))-utcnow()).total_seconds())) if rows else 0

    async def set_domain_cooldown(self,domain,seconds):
        from .models import utcnow
        from datetime import timedelta
        if domain:await self.request('runtime_locks?on_conflict=name','POST',{'name':'domain:'+domain,'owner':'website-rate-limit','expires_at':(utcnow()+timedelta(seconds=max(seconds,60))).isoformat()},'resolution=merge-duplicates')

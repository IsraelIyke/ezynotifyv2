import asyncio
from datetime import timedelta
import json
import logging
import os
from urllib.parse import urlencode
from urllib.parse import urlsplit
import uuid
import aiohttp
from .ai import Groq
from .fetcher import Fetcher
from .models import FetchResult,fetch_key,utcnow
from .processor import process
from .store import Store
from .timefmt import local_timestamp

logger=logging.getLogger(__name__)


async def deliver_notifications(store,session,owner):
    rows=await store.rpc('claim_notifications',{'p_owner':owner,'p_limit':100})
    failed=0
    timezones={}
    for item in rows:
        try:
            # A deleted/paused monitor should not deliver stale queued alerts.
            monitor=await store.request('ezynotify?'+urlencode({'id':'eq.'+item['monitor_id'],'select':'id,paused,checkUpdates,failure_count'}))
            pause_notice=item['payload'].get('_auto_paused') and monitor and monitor[0].get('failure_count',0)>=5
            if not monitor or (monitor[0]['paused'] and not pause_notice) or (item['payload'].get('_auto_paused') and not pause_notice):
                await store.request(f'notification_outbox?id=eq.{item["id"]}&lease_owner=eq.{owner}','PATCH',{'state':'cancelled','lease_owner':None,'lease_until':None});continue
            token=os.getenv('BOT_TOKEN')
            if not token:raise RuntimeError('Alert bot token is not configured')
            payload={**item['payload'],'chat_id':item['chat_id']}
            payload.pop('_auto_paused',None)
            checked_at=payload.pop('_checked_at',item.get('created_at'))
            if checked_at:
                chat=str(item['chat_id'])
                if chat not in timezones:
                    preferences=await store.request('bot_preferences?'+urlencode({'chat_id':'eq.'+chat,'select':'timezone','limit':'1'}))
                    timezones[chat]=preferences[0].get('timezone','UTC') if preferences else 'UTC'
                local=local_timestamp(checked_at,timezones[chat])
                if local:
                    import html
                    payload['text']=payload.get('text','')+'\n\n🕒 '+html.escape(local)
            if not payload.get('text','').startswith(('<b>🔎 KEYWORD WATCH</b>','<b>🔄 PAGE UPDATES</b>','<b>🌐 WEBSITE HEALTH</b>')):
                updates=item['channel']=='updates' or (item['channel']=='main' and monitor[0].get('checkUpdates'))
                payload['text']=('<b>🌐 WEBSITE HEALTH</b>' if item['channel']=='health' else '<b>🔄 PAGE UPDATES</b>' if updates else '<b>🔎 KEYWORD WATCH</b>')+'\n'+payload.get('text','')
            payload['text']=payload.get('text','').replace('\u2014','-')
            async with session.post(f'https://api.telegram.org/bot{token}/sendMessage',json=payload,timeout=aiohttp.ClientTimeout(total=15)) as response:
                result=await response.json()
                if response.status==429:
                    raise RetryDelivery(int(result.get('parameters',{}).get('retry_after',60)))
                if not response.ok or not result.get('ok'):
                    if response.status==403:
                        await store.request(f'notification_outbox?id=eq.{item["id"]}&lease_owner=eq.{owner}','PATCH',{'state':'undeliverable','last_error':'Bot blocked or chat unavailable','lease_owner':None,'lease_until':None});continue
                    raise RuntimeError(f'Telegram delivery failed ({response.status})')
            await store.request(f'notification_outbox?id=eq.{item["id"]}&lease_owner=eq.{owner}','PATCH',{'state':'sent','sent_at':utcnow().isoformat(),'lease_owner':None,'lease_until':None,'last_error':None})
            await asyncio.sleep(0.05)
        except Exception as error:
            failed+=1
            attempts=item['attempts']+1
            delay=max(60*2**min(attempts,8),getattr(error,'delay',0))
            await store.request(f'notification_outbox?id=eq.{item["id"]}&lease_owner=eq.{owner}','PATCH',{
                'attempts':attempts,'state':'failed' if attempts>=20 else 'pending','next_attempt_at':(utcnow()+timedelta(seconds=delay)).isoformat(),
                'lease_owner':None,'lease_until':None,'last_error':'Notification delivery failed; will retry.'})
    return failed


class RetryDelivery(RuntimeError):
    def __init__(self,delay):self.delay=delay


async def run():
    owner=str(uuid.uuid4())
    async with aiohttp.ClientSession() as session:
        store=Store(session)
        if not await store.rpc('acquire_runtime_lock',{'p_name':'worker','p_owner':owner,'p_seconds':1500}):
            logger.info('Another worker is active; queued checks will be picked up later.');return 0
        errors=0
        try:
            errors+=await deliver_notifications(store,session,owner)
            rows=await store.rpc('claim_monitors',{'p_owner':owner,'p_limit':20,'p_monitor_id':os.getenv('MONITOR_ID') or None})
            groups={}
            for row in rows:
                config=row.get('fetch_config') or {'mode':'browser'}
                key=fetch_key(row.get('url') or '',config)
                groups.setdefault(key,[]).append(row)
            ai=Groq(session)
            async with Fetcher(ai=ai,concurrency=min(5,max(1,int(os.getenv('BROWSER_CONCURRENCY','3'))))) as fetcher:
                async def group(key,monitors):
                    nonlocal errors
                    try:
                        row=monitors[0];cached=await store.cache(key)
                        # Cross-job cache avoids duplicate manual checks; no stale
                        # browser snapshot is treated as a fresh successful fetch.
                        fresh=False
                        if cached:
                            from datetime import datetime
                            fresh=(utcnow()-datetime.fromisoformat(cached['fetched_at'].replace('Z','+00:00'))).total_seconds()<45
                        try:domain=urlsplit(row.get('url') or '').hostname
                        except ValueError:domain=None
                        cooldown=await store.domain_cooldown(domain)
                        if cooldown:result=FetchResult('rate_limited',reason='This website requested a pause between checks.',retry_after=cooldown)
                        else:result=FetchResult(**cached['result']) if fresh else await fetcher.fetch(row.get('url') or '',row.get('fetch_config') or {},cached['result'] if cached else None)
                        if result.status=='rate_limited':await store.set_domain_cooldown(domain,result.retry_after)
                        if result.status=='internal_error':errors+=1
                        if result.status=='ok' and not fresh:await store.set_cache(key,result)
                        summaries={}
                        class SharedAI:
                            async def summarize(self,changes):
                                signature=json.dumps(changes,sort_keys=True)
                                if signature not in summaries:summaries[signature]=await ai.summarize(changes)
                                return summaries[signature]
                        for monitor in monitors:
                            try:
                                patch,event,notifications=await process(monitor,result,SharedAI())
                                saved=await store.rpc('finish_monitor',{'p_id':str(monitor['id']),'p_owner':owner,'p_version':monitor['row_version'],'p_patch':patch,'p_event':event,'p_notifications':notifications})
                                logger.info('Monitor %s: %s%s',monitor['id'],result.status,'' if saved else ' (stale result discarded)')
                            except Exception:
                                errors+=1;logger.error('Monitor %s failed while saving its result',monitor['id'])
                    except Exception:
                        errors+=1;logger.error('Fetch group failed; other monitors will continue')
                await asyncio.wait_for(asyncio.gather(*(group(k,v) for k,v in groups.items())),timeout=840)
            errors+=await deliver_notifications(store,session,owner)
            return 1 if errors else 0
        finally:
            # Release unfinished claims after a controlled failure, not after a
            # crash; expiry handles hard-killed workers.
            await store.request('ezynotify?lease_owner=eq.'+owner,'PATCH',{'lease_owner':None,'lease_until':None})
            await store.request('runtime_locks?name=eq.worker&owner=eq.'+owner,'DELETE')


def main():
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    try:return asyncio.run(run())
    except KeyboardInterrupt:return 130
    except Exception:
        logger.error('Worker failed. Verify its configuration and database availability.');return 1

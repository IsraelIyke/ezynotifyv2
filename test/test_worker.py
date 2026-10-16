import unittest
from unittest.mock import AsyncMock,patch
from monitor.worker import run
from monitor.models import FetchResult,utcnow
from monitor.desktop import DesktopUnavailable


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_routing_cache_isolation_and_desktop_failure_preserves_attempts(self):
        rows=[{'id':i,'url':'https://example.com','row_version':1,'failure_count':failures,'fetch_config':config}
              for i,failures,config in [(1,0,{}),(2,3,{}),(3,4,{'screenshot':True})]]
        saved=[]
        store=AsyncMock()
        async def rpc(name,args):
            if name=='acquire_runtime_lock':return True
            if name=='claim_monitors':return rows
            if name=='finish_monitor':saved.append(args);return True
        store.rpc.side_effect=rpc
        store.domain_cooldown.return_value=0
        store.cache.return_value={'fetched_at':utcnow().isoformat(),'result':FetchResult('ok',text='Cached').cached()}
        fetcher=AsyncMock();fetcher.__aenter__.return_value=fetcher
        fetcher.fetch_advanced.side_effect=[FetchResult('timeout',reason='Website timeout'),DesktopUnavailable('No desktop')]
        with patch('monitor.worker.Store',return_value=store),patch('monitor.worker.Fetcher',return_value=fetcher),patch('monitor.worker.deliver_notifications',new=AsyncMock(return_value=0)):
            result=await run()
        self.assertEqual(result,1)
        self.assertEqual(store.cache.await_count,1)
        self.assertEqual(fetcher.fetch_advanced.await_count,2)
        fetcher.fetch.assert_not_awaited()
        updates={s['p_id']:s['p_patch'] for s in saved}
        self.assertEqual(updates['1']['failure_count'],0)
        self.assertEqual(updates['2']['failure_count'],4)
        self.assertNotIn('3',updates)
        deferred=[c for c in store.request.await_args_list if len(c.args)>2 and 'next_check_at' in c.args[2]]
        self.assertEqual(len(deferred),1)
        self.assertNotIn('failure_count',deferred[0].args[2])

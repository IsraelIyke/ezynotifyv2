import unittest
from unittest.mock import AsyncMock
from monitor.fetcher import Fetcher
from monitor.models import FetchResult
from monitor.processor import process


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row={'id':1,'url':'https://example.com','title':'Site','fetch_config':{'mode':'health'},'interval_minutes':10}
        self.ai=AsyncMock()

    async def test_outage_alert_once_then_recovery_and_another_outage(self):
        row=dict(self.row)
        patch,_,notices=await process(row,FetchResult('ok',http_status=200),self.ai)
        self.assertEqual(notices,[]);row.update(patch)
        for i in range(7):
            patch,_,notices=await process(row,FetchResult('health_down',http_status=503,reason='HTTP 503'),self.ai)
            self.assertEqual(len(notices),1 if i==0 else 0)
            self.assertNotIn('paused',patch);row.update(patch)
        patch,_,notices=await process(row,FetchResult('ok',http_status=200),self.ai)
        self.assertEqual(len(notices),1);self.assertIn('back online',notices[0]['payload']['text'])
        self.assertIn('WEBSITE HEALTH',notices[0]['payload']['text']);self.assertEqual(patch['failure_count'],0)
        row.update(patch);_,_,notices=await process(row,FetchResult('health_down',reason='HTTP 503'),self.ai)
        self.assertEqual(len(notices),1);self.ai.summarize.assert_not_called()

    async def test_restriction_does_not_falsely_recover_or_declare_an_outage(self):
        row={**self.row,'reference':'health:down'}
        patch,_,notices=await process(row,FetchResult('blocked',reason='HTTP 403'),self.ai)
        self.assertNotIn('reference',patch);self.assertEqual(patch['last_status'],'health_unknown')
        self.assertIn('inconclusive',notices[0]['payload']['text']);row.update(patch)
        _,_,notices=await process(row,FetchResult('blocked',reason='HTTP 403'),self.ai)
        self.assertEqual(notices,[])

    async def test_http_classification_redirects_and_private_redirect_rejection(self):
        f=Fetcher()
        for status,expected in [(200,'ok'),(204,'ok'),(404,'health_down'),(503,'health_down'),(403,'blocked'),(401,'blocked'),(429,'rate_limited')]:
            f.health_request=AsyncMock(return_value=(status,{}))
            self.assertEqual((await f.health_page('https://example.com')).status,expected)
        f.health_request=AsyncMock(side_effect=[(301,{'Location':'/status'}),(200,{})])
        self.assertEqual((await f.health_page('https://example.com')).status,'ok')
        f.health_request=AsyncMock(return_value=(302,{'Location':'http://127.0.0.1/private'}))
        result=await f.fetch('https://example.com',{'mode':'health'})
        self.assertEqual(result.status,'unsafe_url')

    async def test_network_failure_is_reported_as_down(self):
        patch,_,notices=await process(self.row,FetchResult('network_error',reason='DNS or connection failed'),self.ai)
        self.assertEqual(patch['last_status'],'health_down');self.assertEqual(len(notices),1)

    async def test_probe_does_not_read_response_body(self):
        class Response:
            status=200
            headers={}
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Session:
            def get(self,*args,**kwargs):return Response()
        f=Fetcher();f.http=Session()
        self.assertEqual(await f.health_request('https://example.com'),(200,{}))

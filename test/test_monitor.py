import asyncio
from datetime import datetime,timezone,timedelta
import socket
import unittest
from unittest.mock import AsyncMock,patch
from monitor.models import FetchResult
from monitor.processor import process,get_diff
from monitor.fetcher import challenge_signal,html_text,retry_seconds
from monitor.security import validate_url,public_ip,PublicResolver,UnsafeDestination
from monitor.ai import Groq


class ProcessorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row={'id':1,'url':'https://example.com','title':'News','reference':'Old content.',
            'checkUpdates':True,'shouldContinueCheck':True,'interval_minutes':10,'shouldSendDetailedUpdates':True}
        self.ai=AsyncMock();self.ai.summarize.return_value=('The notice changed.','groq')

    async def test_failed_fetch_never_replaces_reference(self):
        for status in ['challenge','timeout','blocked','empty','unsafe_url']:
            patch,event,alerts=await process(self.row,FetchResult(status,reason='Unavailable'),self.ai)
            self.assertNotIn('reference',patch);self.ai.summarize.assert_not_called()

    async def test_failure_notice_once_and_recovery_notice(self):
        row={**self.row,'failure_count':0}
        patch,event,alerts=await process(row,FetchResult('challenge',reason='Verification'),self.ai)
        self.assertEqual(len(alerts),1)
        row.update(patch)
        patch,event,alerts=await process(row,FetchResult('challenge',reason='Verification'),self.ai)
        self.assertEqual(alerts,[])
        patch,event,alerts=await process(row,FetchResult('ok',text='Old content.'),self.ai)
        self.assertEqual(len(alerts),1);self.assertFalse(patch['failure_notified'])

    async def test_groq_primary_summary_and_one_shot_completion(self):
        row={**self.row,'shouldContinueCheck':False}
        patch,event,alerts=await process(row,FetchResult('ok',text='New content. Appended sentence.'),self.ai)
        self.ai.summarize.assert_awaited_once();self.assertTrue(patch['completed'])
        self.assertIn('The notice changed.',alerts[0]['payload']['text']);self.assertEqual(event['details']['source'],'groq')

    async def test_fifth_consecutive_failure_pauses_and_notifies_even_after_previous_notice(self):
        row={**self.row,'failure_count':3,'failure_notified':True}
        patch,_,alerts=await process(row,FetchResult('timeout',reason='Unavailable'),self.ai)
        self.assertNotIn('paused',patch);self.assertEqual(alerts,[])
        row.update(patch)
        patch,event,alerts=await process(row,FetchResult('timeout',reason='Unavailable'),self.ai)
        self.assertTrue(patch['paused']);self.assertEqual(patch['failure_count'],5)
        self.assertNotIn('reference',patch);self.assertTrue(event['details']['auto_paused'])
        self.assertEqual(len(alerts),1);self.assertTrue(alerts[0]['payload']['_auto_paused'])
        self.assertIn('Resume',alerts[0]['payload']['text'])

    async def test_success_resets_failure_streak(self):
        patch,_,_=await process({**self.row,'failure_count':4},FetchResult('ok',text='Old content.'),self.ai)
        self.assertEqual(patch['failure_count'],0);self.assertNotIn('paused',patch)

    async def test_advanced_stage_starts_after_three_and_waits_twelve_hours(self):
        now=datetime(2026,10,16,tzinfo=timezone.utc)
        row={**self.row,'failure_count':2,'failure_notified':True,'interval_minutes':1440}
        update,event,alerts=await process(row,FetchResult('timeout',reason='Unavailable'),self.ai,now)
        self.assertEqual(update['next_check_at'],now.isoformat())
        self.assertTrue(event['details']['advanced_pending'])
        self.assertIn('Advanced search',alerts[0]['payload']['text'])
        row.update(update)
        update,event,_=await process(row,FetchResult('timeout',reason='Unavailable'),self.ai,now)
        self.assertEqual(update['next_check_at'],(now+timedelta(hours=12)).isoformat())
        self.assertEqual(event['details']['search_stage'],'advanced')
        self.assertNotIn('reference',update)
        update,_,_=await process(row,FetchResult('rate_limited',retry_after=86400),self.ai,now)
        self.assertEqual(update['next_check_at'],(now+timedelta(days=1)).isoformat())

    async def test_baseline_does_not_alert_or_invoke_ai(self):
        row={**self.row,'reference':''}
        patch,event,alerts=await process(row,FetchResult('ok',text='First content'),self.ai)
        self.assertEqual(alerts,[]);self.assertEqual(event['status'],'baseline');self.ai.summarize.assert_not_called()

    async def test_keywords_rearm_after_disappearance_without_destroying_configuration(self):
        row={**self.row,'checkUpdates':False,'keywords':{'keywords':['applications open']}}
        patch,event,alerts=await process(row,FetchResult('ok',text='Applications OPEN today'),self.ai)
        self.assertEqual(len(alerts),1);self.assertNotIn('keywords',patch)
        row.update(patch)
        patch,event,alerts=await process(row,FetchResult('ok',text='Applications OPEN today'),self.ai)
        self.assertEqual(alerts,[])
        row.update(patch);patch,_,_=await process(row,FetchResult('ok',text='Closed today'),self.ai)
        row.update(patch);_,_,alerts=await process(row,FetchResult('ok',text='applications open'),self.ai)
        self.assertEqual(len(alerts),1)

    async def test_transient_failures_notify_only_after_three_checks_and_honor_retry_after(self):
        row={**self.row,'failure_count':1}
        now=datetime(2026,10,16,tzinfo=timezone.utc)
        patch,_,alerts=await process(row,FetchResult('rate_limited',reason='Slow down',retry_after=7200),self.ai,now)
        self.assertEqual(alerts,[]);self.assertGreaterEqual((datetime.fromisoformat(patch['next_check_at'])-now).total_seconds(),7200)
        row.update(patch);_,_,alerts=await process(row,FetchResult('rate_limited',reason='Slow down'),self.ai,now)
        self.assertEqual(len(alerts),1)

    async def test_groq_failure_uses_factual_fallback(self):
        ai=Groq(None,key='',model='')
        summary,source=await ai.summarize([{'removed':'old','added':'new'}])
        self.assertEqual(source,'fallback');self.assertIn('Removed: old',summary)


class ExtractionTests(unittest.TestCase):
    def test_added_deleted_and_replaced_trailing_content(self):
        self.assertEqual(get_diff('a b','a b c'),[{'removed':'','added':'c'}])
        self.assertEqual(get_diff('a b c','a b'),[{'removed':'c','added':''}])
        self.assertEqual(get_diff('a b','a z'),[{'removed':'b','added':'z'}])

    def test_challenge_signals_do_not_flag_an_ordinary_embedded_widget(self):
        self.assertFalse(challenge_signal('Welcome to our news page','<script src="recaptcha.js"></script>'))
        self.assertTrue(challenge_signal('Verify you are human','<div class="cf-turnstile"></div>'))
        self.assertTrue(challenge_signal('Any response',headers={'cf-mitigated':'challenge'}))

    def test_target_and_ignored_regions(self):
        text=html_text('<body><nav>Menu</nav><main>News <span class="clock">12:00</span></main><footer>Footer</footer></body>',{'selector':'main','ignore':'.clock'})
        self.assertEqual(text,'News')

    def test_public_destination_rules(self):
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','0.0.0.0','::1','::ffff:127.0.0.1','224.0.0.1']:
            self.assertFalse(public_ip(address),address)
        self.assertTrue(public_ip('1.1.1.1'))
        for url in ['file:///x','http://localhost','http://192.168.1.1','http://[::1]','http://metadata.internal','http://a:b@example.com','https://example.com:1234']:
            with self.assertRaises(UnsafeDestination,msg=url):validate_url(url)


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_domain_retry_after_prevents_more_requests(self):
        import time
        from monitor.fetcher import Fetcher
        fetcher=Fetcher();fetcher.domain_retry_until['example.com']=time.monotonic()+3600
        fetcher.request=AsyncMock()
        result=await fetcher.fetch('https://example.com/another-page',{})
        self.assertEqual(result.status,'rate_limited');self.assertGreater(result.retry_after,3500)
        fetcher.request.assert_not_called()

    async def test_dns_rebinding_and_mixed_private_records_are_blocked(self):
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch.object(asyncio.get_running_loop(),'getaddrinfo',new=AsyncMock(return_value=records)):
            with self.assertRaises(UnsafeDestination):await PublicResolver().resolve('example.com',443)


if __name__=='__main__':unittest.main()

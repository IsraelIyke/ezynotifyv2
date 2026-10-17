import unittest
from unittest.mock import AsyncMock,patch
from monitor.fetcher import Fetcher,DownloadTooLarge,MAX_DOCUMENT_BYTES
from monitor.processor import process,get_diff


class Stream:
    def __init__(self,size):self.size=size
    async def iter_chunked(self,size):
        for offset in range(0,self.size,size):yield b'x'*min(size,self.size-offset)


class Response:
    status=200
    def __init__(self,size,content_type):
        self.headers={'Content-Type':content_type};self.content=Stream(size)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass


class Session:
    def __init__(self,response):self.response=response
    def request(self,*args,**kwargs):return self.response


class LargePageTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_limit_accepts_large_markup_but_keeps_assets_bounded(self):
        fetcher=Fetcher()
        fetcher.http=Session(Response(3_200_000,'text/html; charset=utf-8'))
        self.assertEqual(len((await fetcher.request('https://fixture.test'))[2]),3_200_000)
        fetcher.http=Session(Response(3_200_000,'image/jpeg'))
        with self.assertRaises(DownloadTooLarge):await fetcher.request('https://fixture.test')
        fetcher.http=Session(Response(MAX_DOCUMENT_BYTES+1,'text/html'))
        with self.assertRaises(DownloadTooLarge):await fetcher.request('https://fixture.test')

    async def test_keyword_at_end_of_large_page_is_preserved_in_http_and_browser(self):
        body=('Archived public announcement. '*6000)+' FINAL KEYWORD'
        markup=('<html><body><main>'+body+'</main><!--'+'x'*3_100_000+'--></body></html>').encode()
        class LargeFetcher(Fetcher):
            async def request(self,*args,**kwargs):return 200,{'Content-Type':'text/html'},markup
        async with LargeFetcher() as fetcher:
            for mode in ['html','browser']:
                result=await fetcher.fetch('https://fixture.test',{'mode':mode})
                self.assertEqual(result.status,'ok',result.reason)
                self.assertGreater(len(result.text),120_000)
                self.assertTrue(result.text.endswith('FINAL KEYWORD'))
                row={'url':'https://fixture.test','keywords':{'keywords':['final keyword']}}
                update,_,alerts=await process(row,result,AsyncMock())
                self.assertEqual(update['reference'],result.text)
                self.assertIn('final keyword',alerts[0]['payload']['text'])

    async def test_browser_total_budget_is_enforced(self):
        class Fixture(Fetcher):
            async def request(self,*args,**kwargs):return 200,{'Content-Type':'text/html'},b'<html><body>Public content</body></html>'
        async with Fixture() as fetcher:
            with patch('monitor.fetcher.MAX_BROWSER_BYTES',10):
                result=await fetcher.fetch('https://fixture.test',{'mode':'browser'})
        self.assertEqual(result.status,'resource_limit')

    async def test_http_download_limit_is_reported_as_too_large(self):
        fetcher=Fetcher();fetcher.request=AsyncMock(side_effect=DownloadTooLarge(MAX_DOCUMENT_BYTES))
        result=await fetcher.fetch('https://fixture.test',{'mode':'html'})
        self.assertEqual(result.status,'too_large');self.assertIn('20 MB',result.reason)


class LargeDiffTests(unittest.TestCase):
    def test_small_edit_in_large_document_keeps_precise_added_text(self):
        old=' '.join('word'+str(i) for i in range(25_000))
        new=old+' Newly published announcement'
        self.assertEqual(get_diff(old,new),[{'removed':'','added':'Newly published announcement'}])

    def test_large_replacement_is_complete_and_bypasses_quadratic_alignment(self):
        old=' '.join('old'+str(i) for i in range(15_000))
        new=' '.join('new'+str(i) for i in range(15_000))
        with patch('monitor.processor.difflib.SequenceMatcher',side_effect=AssertionError('Detailed alignment must stay bounded')):
            self.assertEqual(get_diff(old,new),[{'removed':old,'added':new}])

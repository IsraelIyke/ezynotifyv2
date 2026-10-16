"""Real Chromium regression tests with in-memory HTTP fixtures (no live sites)."""
import unittest
from monitor.fetcher import Fetcher


class FixtureFetcher(Fetcher):
    def __init__(self):
        super().__init__();self.requests=[]

    async def request(self,url,headers=None,method='GET',data=None):
        self.requests.append(url)
        if url.endswith('/robots.txt'):return 200,{'Content-Type':'text/plain'},b'User-agent: *\nAllow: /\n'
        pages={
          '/dynamic': '<html><body><main>Loading...</main><script>setTimeout(()=>document.querySelector("main").textContent="Admissions open today",3500)</script></body></html>',
          '/framed': '<html><body><h1>Parent</h1><iframe id="news" src="https://fixture.test/frame"></iframe></body></html>',
          '/frame': '<html><body><main>Frame announcement</main></body></html>',
          '/shadow': '<html><body><main id="host"></main><script>document.querySelector("main").attachShadow({mode:"open"}).innerHTML="<p>Shadow announcement</p>"</script></body></html>',
          '/challenge': '<html><body>Verify you are human<div class="cf-turnstile"></div></body></html>',
          '/missing': '<html><body>No target</body></html>',
          '/normal': '<html><body><nav>Menu</nav><main>Welcome <span class="clock">12:00</span></main></body></html>',
          '/redirect': '',
          '/data': '{"price": 42, "available": true}',
        }
        from urllib.parse import urlsplit
        path=urlsplit(url).path
        if path=='/redirect':return 302,{'Location':'http://127.0.0.1/private'},b''
        if path=='/data':return 200,{'Content-Type':'application/json'},pages[path].encode()
        return 200,{'Content-Type':'text/html'},pages.get(path,'').encode()


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.fetcher=await FixtureFetcher().__aenter__()
    async def asyncTearDown(self):await self.fetcher.__aexit__()

    async def test_waits_for_slow_dynamic_content(self):
        result=await self.fetcher.fetch('https://fixture.test/dynamic',{'mode':'browser','selector':'main'})
        self.assertEqual(result.status,'ok');self.assertEqual(result.text,'Admissions open today')

    async def test_iframe_content(self):
        result=await self.fetcher.fetch('https://fixture.test/framed',{'mode':'browser','frame':'iframe#news','selector':'main'})
        self.assertEqual(result.status,'ok');self.assertEqual(result.text,'Frame announcement')

    async def test_open_shadow_root(self):
        result=await self.fetcher.fetch('https://fixture.test/shadow',{'mode':'browser','selector':'main'})
        self.assertEqual(result.status,'ok');self.assertEqual(result.text,'Shadow announcement')

    async def test_challenge_is_not_content(self):
        result=await self.fetcher.fetch('https://fixture.test/challenge',{'mode':'browser'})
        self.assertEqual(result.status,'challenge');self.assertEqual(result.text,'')

    async def test_ignored_regions(self):
        result=await self.fetcher.fetch('https://fixture.test/normal',{'mode':'browser','selector':'main','ignore':'.clock'})
        self.assertEqual(result.text,'Welcome')

    async def test_http_redirect_cannot_reach_private_destination(self):
        result=await self.fetcher.fetch('https://fixture.test/redirect',{'mode':'html'})
        self.assertEqual(result.status,'unsafe_url');self.assertTrue(all('127.0.0.1' not in s for s in self.fetcher.requests))

    async def test_browser_redirect_cannot_reach_private_destination(self):
        result=await self.fetcher.fetch('https://fixture.test/redirect',{'mode':'browser'})
        self.assertNotEqual(result.status,'ok');self.assertTrue(all('127.0.0.1' not in s for s in self.fetcher.requests))

    async def test_json_source(self):
        result=await self.fetcher.fetch('https://fixture.test/data',{'mode':'feed'})
        self.assertEqual(result.status,'ok');self.assertIn('42',result.text)


if __name__=='__main__':unittest.main()

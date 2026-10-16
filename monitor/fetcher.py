"""Bounded browser/HTTP fetches with public-only egress and challenge detection."""
import asyncio
import base64
from collections import defaultdict
from email.utils import parsedate_to_datetime
import json
import re
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from .models import FetchResult, normalize, utcnow
from .security import PublicResolver, validate_url, UnsafeDestination

MAX_BYTES=3_000_000
MAX_TEXT=120_000
CRAWLER='EzyNotify'


def challenge_signal(text,html='',headers=None):
    headers={k.lower():v for k,v in (headers or {}).items()}
    if headers.get('cf-mitigated')=='challenge' or headers.get('x-vercel-mitigated')=='challenge':return True
    lower=text.lower()
    markers=('verify you are human','verify that you are human','checking your browser','performing security verification','complete the security check','unusual traffic from your computer','enable javascript and cookies to continue')
    # An embedded captcha script alone is not evidence that page access is blocked.
    widget=any(x in html.lower() for x in ('g-recaptcha','h-captcha','cf-turnstile','challenge-platform'))
    return any(x in lower for x in markers) and (widget or len(text)<5000)


def retry_seconds(value):
    try:return max(0,min(int(value),86400))
    except (ValueError,TypeError):
        try:return max(0,min(int((parsedate_to_datetime(value)-utcnow()).total_seconds()),86400))
        except (ValueError,TypeError,OverflowError):return 0


def html_text(html,config):
    soup=BeautifulSoup(html,'html.parser')
    for item in soup.select('script,style,noscript,template,nav,footer,[aria-hidden="true"]'):
        item.decompose()
    if config.get('ignore'):
        for item in soup.select(config['ignore']):item.decompose()
    target=soup.select(config['selector']) if config.get('selector') else [soup.body or soup]
    return normalize('\n'.join(item.get_text('\n',strip=True) for item in target))


class Fetcher:
    def __init__(self,ai=None,concurrency=3):
        self.ai=ai
        self.semaphore=asyncio.Semaphore(concurrency)
        self.domain_locks=defaultdict(asyncio.Lock)
        self.last_request={}
        self.robots={}
        self.playwright=None
        self.browser=None

    async def __aenter__(self):
        self.http=aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver(),use_dns_cache=False,limit=12),
            timeout=aiohttp.ClientTimeout(total=20,connect=8),trust_env=False)
        self.playwright=await async_playwright().start()
        self.browser=await self.playwright.chromium.launch(headless=True,args=['--disable-quic','--disable-background-networking'])
        return self

    async def __aexit__(self,*args):
        if self.browser:await self.browser.close()
        if self.playwright:await self.playwright.stop()
        await self.http.close()

    async def request(self,url,headers=None,method='GET',data=None):
        url=validate_url(url)
        async with self.http.request(method,url,headers=headers,data=data,allow_redirects=False) as response:
            body=bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body)>MAX_BYTES:raise ValueError('The page exceeds the supported download size.')
            return response.status,dict(response.headers),bytes(body)

    async def allowed(self,url):
        parsed=urlsplit(validate_url(url));origin=f'{parsed.scheme}://{parsed.netloc}'
        if origin not in self.robots:
            try:
                robots_url=origin+'/robots.txt'
                for _ in range(4):
                    status,headers,body=await self.request(robots_url,{'User-Agent':CRAWLER+'/3.0'})
                    if status not in (301,302,303,307,308):break
                    location=headers.get('Location') or headers.get('location')
                    if not location:break
                    robots_url=validate_url(urljoin(robots_url,location))
                if status in (404,410):self.robots[origin]=(None,2)
                elif status!=200:raise ValueError('robots.txt could not be checked; retrying later.')
                else:
                    parser=RobotFileParser();parser.parse(body.decode('utf-8','replace').splitlines())
                    self.robots[origin]=(parser,max(2,min(parser.crawl_delay(CRAWLER) or parser.crawl_delay('*') or 2,60)))
            except (aiohttp.ClientError,asyncio.TimeoutError):raise ValueError('robots.txt could not be checked; retrying later.') from None
        parser,delay=self.robots[origin]
        return (not parser or parser.can_fetch(CRAWLER,url)),delay

    async def http_page(self,url,config,cache):
        headers={'User-Agent':CRAWLER+'/3.0','Accept':'text/html,application/json,application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.5'}
        if cache and cache.get('status')=='ok':
            if cache.get('etag'):headers['If-None-Match']=cache['etag']
            if cache.get('modified'):headers['If-Modified-Since']=cache['modified']
        for _ in range(6):
            allowed,_=await self.allowed(url)
            if not allowed:return FetchResult('robots_denied',reason='This website disallows automated checks for this page.')
            status,response_headers,body=await self.request(url,headers)
            if status in (301,302,303,307,308):
                location=response_headers.get('Location') or response_headers.get('location')
                if not location:raise ValueError('Redirect has no destination.')
                url=validate_url(urljoin(url,location));continue
            text=body.decode('utf-8','replace')
            if status==304 and cache and cache.get('text'):
                return FetchResult(**{**cache,'final_url':url})
            if challenge_signal(text,text,response_headers):return FetchResult('challenge',reason='The website requires human verification.',http_status=status,final_url=url)
            if status in (401,403):return FetchResult('blocked',reason='The website denied automated access or requires a login.',http_status=status)
            if status==429:return FetchResult('rate_limited',reason='The website asked us to slow down.',http_status=status,retry_after=retry_seconds(response_headers.get('Retry-After')))
            if status>=400:return FetchResult('http_error',reason=f'The website returned HTTP {status}.',http_status=status)
            content_type=response_headers.get('Content-Type',response_headers.get('content-type','')).lower()
            if 'json' in content_type:
                content=json.dumps(json.loads(text),ensure_ascii=False,sort_keys=True,indent=2)
            elif any(x in content_type for x in ('xml','rss','atom')):
                # XML parsing must never resolve external entities.
                from defusedxml import ElementTree
                root=ElementTree.fromstring(body)
                content='\n'.join(s.strip() for s in root.itertext() if s.strip())
            elif 'html' in content_type or not content_type:
                content=html_text(text,config)
            elif content_type.startswith('text/'):
                content=text
            else:return FetchResult('unsupported',reason='Use a public HTML, text, RSS/Atom, or JSON page.')
            content=normalize(content)
            if not content:return FetchResult('empty',reason='The page has no readable content, or the target selector did not match.')
            if len(content)>MAX_TEXT:return FetchResult('too_large',reason='Choose a smaller page region with a target selector.')
            if self.ai and ('html' in content_type) and (len(content)<300 or any(s in content.lower() for s in ('access denied','security check','sign in to continue'))):
                classification=await self.ai.classify(content)
                if classification:return FetchResult(classification,reason='The page appears to require verification or is unavailable.')
            return FetchResult('ok',content,url,http_status=status,etag=response_headers.get('ETag',''),modified=response_headers.get('Last-Modified',''))
        return FetchResult('http_error',reason='Too many redirects.')

    async def browser_page(self,url,config):
        context=await self.browser.new_context(viewport={'width':1440,'height':1000},service_workers='block',accept_downloads=False)
        page=await context.new_page()
        request_count=0
        blocked_navigation=False
        async def route_request(route):
            nonlocal request_count,blocked_navigation
            request=route.request;request_count+=1
            try:
                target=validate_url(request.url)
                if request_count>120 or request.resource_type in ('media','font') or request.method not in ('GET','HEAD'):
                    await route.abort();return
                if request.is_navigation_request():
                    allowed,_=await self.allowed(target)
                    if not allowed:blocked_navigation=True;await route.abort();return
                # Fulfill ALL browser traffic through the validated resolver. No
                # browser-side DNS, inherited cookies, or unvalidated redirects.
                headers={k:v for k,v in (await request.all_headers()).items() if k.lower() not in ('host','connection','accept-encoding','authorization','proxy-authorization')}
                status,response_headers,body=await self.request(target,headers,request.method)
                cleaned={k:v for k,v in response_headers.items() if k.lower() not in ('content-encoding','transfer-encoding','content-length','connection')}
                await route.fulfill(status=status,headers=cleaned,body=body)
            except (UnsafeDestination,aiohttp.ClientError,asyncio.TimeoutError,ValueError):
                if request.is_navigation_request():blocked_navigation=True
                await route.abort()
        await context.route('**/*',route_request)
        await context.route_web_socket('**/*',lambda ws:ws.close())
        try:
            response=await page.goto(url,wait_until='domcontentloaded',timeout=30000)
            if blocked_navigation:return FetchResult('blocked',reason='A navigation destination was disallowed or unavailable.')
            status=response.status if response else 0
            response_headers=await response.all_headers() if response else {}
            body=await page.locator('body').inner_text(timeout=10000)
            html=await page.content()
            if challenge_signal(body,html,response_headers):return FetchResult('challenge',reason='The website requires human verification.',final_url=page.url,http_status=status)
            if status in (401,403):return FetchResult('blocked',reason='The website denied access or requires a login.',http_status=status)
            if status==429:return FetchResult('rate_limited',reason='The website asked us to slow down.',http_status=status,retry_after=retry_seconds(response_headers.get('retry-after')))
            if status>=400:return FetchResult('http_error',reason=f'The website returned HTTP {status}.',http_status=status)
            root=page
            if config.get('frame'):
                handle=await page.locator(config['frame']).first.element_handle(timeout=10000)
                root=await handle.content_frame() if handle else None
                if root is None:return FetchResult('empty',reason='The configured iframe is unavailable.')
            target=root.locator(config.get('selector') or 'body').first
            await target.wait_for(state='visible',timeout=15000)
            if config.get('scroll'):
                for _ in range(5):
                    await root.evaluate('window.scrollBy(0, window.innerHeight)')
                    await asyncio.sleep(0.5)
            # Read cloned DOM to avoid interfering with the site's live scripts.
            # Open shadow roots are included; closed roots remain inaccessible.
            async def extract():
                return normalize(await target.evaluate('''(node, ignored) => {
                  const hidden = 'script,style,noscript,template,nav,footer,[aria-hidden="true"]';
                  const walk = (el) => {
                    if(el.nodeType === Node.TEXT_NODE) return el.textContent;
                    if(el.nodeType !== Node.ELEMENT_NODE) return '';
                    if(el.matches(hidden) || (ignored && el.matches(ignored))) return '';
                    const style=getComputedStyle(el);
                    if(style.display==='none' || style.visibility==='hidden') return '';
                    return [...(el.shadowRoot || el).childNodes].map(walk).join(style.display==='inline'?' ':'\\n');
                  };
                  return walk(node);
                }''',config.get('ignore') or ''))
            previous='';stable=0;deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                content=await extract()
                loading=bool(re.fullmatch(r'(loading[.\s…]*|please wait[.\s…]*)',content,re.I))
                stable=stable+1 if content and content==previous and not loading else 0
                if stable>=3:break
                previous=content;await asyncio.sleep(0.75)
            else:return FetchResult('timeout',reason='The selected content did not become stable before the timeout.')
            # Some sites display a challenge only after the initial JS bootstrap.
            body=await page.locator('body').inner_text(timeout=3000)
            if request_count>120:return FetchResult('resource_limit',reason='This page exceeded the browser request limit; use a feed/API or a simpler page.')
            if challenge_signal(body,await page.content(),response_headers):return FetchResult('challenge',reason='The website requires human verification.')
            if await page.locator('input[type="password"]').count() and len(content)<2000:return FetchResult('login',reason='The page requires a login.')
            suspicious=len(content)<300 or any(x in (await page.title()).lower() for x in ('access denied','just a moment','security check','error'))
            if suspicious and self.ai:
                screenshot=base64.b64encode(await page.screenshot(full_page=False)).decode() if self.ai.vision_model else ''
                classification=await self.ai.classify(body,screenshot)
                if classification:return FetchResult(classification,reason='The page appears to require verification or is unavailable.')
            if not content:return FetchResult('empty',reason='No readable content was found.')
            if len(content)>MAX_TEXT:return FetchResult('too_large',reason='Choose a smaller page region with a target selector.')
            return FetchResult('ok',content,page.url,http_status=status)
        except BrowserTimeout:
            return FetchResult('timeout',reason='The page or selected content did not become ready in time.')
        finally:await context.close()

    async def fetch(self,url,config,cache=None):
        try:url=validate_url(url)
        except UnsafeDestination as error:return FetchResult('unsafe_url',reason=str(error))
        domain=urlsplit(url).hostname
        async with self.semaphore,self.domain_locks[domain]:
            try:
                allowed,delay=await self.allowed(url)
                if not allowed:return FetchResult('robots_denied',reason='This website disallows automated checks for this page.')
                await asyncio.sleep(max(0,delay-(time.monotonic()-self.last_request.get(domain,0))))
                for attempt in range(2):
                    async with asyncio.timeout(100):
                        result=await (self.browser_page(url,config) if config.get('mode','browser')=='browser' else self.http_page(url,config,cache))
                    if result.status not in ('timeout','http_error') or attempt:break
                    await asyncio.sleep(2)
                return result
            except UnsafeDestination as error:return FetchResult('unsafe_url',reason=str(error))
            except (asyncio.TimeoutError,aiohttp.ClientError):return FetchResult('timeout',reason='The website could not be reached before the timeout.')
            except Exception:
                return FetchResult('error',reason='The page could not be read. Check the URL and advanced selectors.')
            finally:self.last_request[domain]=time.monotonic()

"""Optional, bounded screenshots from the same page used for the text check."""
import asyncio
import base64
import re
from .models import normalize


async def capture_alert_images(page,target,content,config):
    from .processor import get_diff
    if not config.get('screenshot'):return {}
    row=config.get('_monitor') or {}
    requests={}
    seen=row.get('keywords_seen') or []
    words=[str(k).casefold() for k in (row.get('keywords') or {}).get('keywords',[])]
    matches=[k for k in words if k in content.casefold() and k not in seen]
    if matches:requests['keywords']=matches
    old=row.get('reference') or ''
    if row.get('checkUpdates') and old and normalize(old).casefold()!=normalize(content).casefold():
        requests['updates']=[c['added'] for c in get_diff(old,content) if c['added']][:20]
    images={}
    for channel,phrases in requests.items():
        try:
            async with asyncio.timeout(12):
                region=None
                for phrase in phrases:
                    pattern=re.compile(r'\s+'.join(re.escape(word) for word in phrase[:240].split()),re.I)
                    candidate=target.get_by_text(pattern).first
                    if await candidate.count() and await candidate.is_visible():
                        region=candidate;break
                highlighted=region is not None
                region=region or target
                await region.scroll_into_view_if_needed(timeout=3000)
                original=await region.get_attribute('style')
                try:
                    if highlighted:
                        await region.evaluate("el => { el.style.setProperty('outline','3px solid #e59700','important'); el.style.setProperty('background-color','#fff29a','important'); el.style.setProperty('color','#171717','important'); }")
                    box=await region.bounding_box()
                    if box and box['width']<=1440 and box['height']<=900 and 1/19<=box['width']/max(1,box['height'])<=19:
                        data=await region.screenshot(type='jpeg',quality=80,timeout=4000,animations='disabled')
                    else:
                        data=await page.screenshot(type='jpeg',quality=80,full_page=False,timeout=4000,animations='disabled')
                    if len(data)>1_000_000:continue
                    note='First matching region highlighted.' if highlighted else 'Current monitored area; removed text or an exact match could not be highlighted.'
                    images[channel]={'image':base64.b64encode(data).decode(),'note':note}
                finally:
                    await region.evaluate("(el,style) => style===null ? el.removeAttribute('style') : el.setAttribute('style',style)",original)
        except Exception:
            # A screenshot must never turn a successful text check into a failure.
            continue
    return images

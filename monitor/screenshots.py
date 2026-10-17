"""Full-page success images with temporary highlights on every located match."""
import asyncio
import base64
import difflib
import uuid
from .models import normalize


HIGHLIGHT = r'''(target, {phrases, ignored, token}) => {
  const doc=target.ownerDocument, win=doc.defaultView;
  const chars=[], positions=[];
  const space=()=>{if(chars.length && chars[chars.length-1]!==' '){chars.push(' ');positions.push(null);}};
  const walk=node=>{
    if(node.nodeType===Node.TEXT_NODE){
      for(let i=0;i<node.textContent.length;i++){
        const ch=node.textContent[i];
        if(/\s/u.test(ch)){space();continue;}
        chars.push(ch);positions.push([node,i]);
      }
      return;
    }
    if(node.nodeType!==Node.ELEMENT_NODE)return;
    if(node.matches('script,style,noscript,template,nav,footer,[aria-hidden="true"]') || (ignored&&node.matches(ignored)))return;
    const style=win.getComputedStyle(node);
    if(style.display==='none'||style.visibility==='hidden')return;
    const block=style.display!=='inline' || node.tagName==='BR';
    if(block)space();
    for(const child of (node.shadowRoot||node).childNodes)walk(child);
    if(block)space();
  };
  walk(target);
  const text=chars.join('');let matches=0;
  for(const entry of phrases){
    const phrase=(typeof entry==='string'?entry:entry.text).replace(/\s+/gu,' ').trim();
    if(!phrase)continue;
    const escaped=phrase.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const regex=new RegExp(escaped,'giu');
    for(const match of text.matchAll(regex)){
      if(typeof entry!=='string' && (!text.slice(0,match.index).trimEnd().endsWith(entry.before) || !text.slice(match.index+match[0].length).trimStart().startsWith(entry.after)))continue;
      const start=positions[match.index],end=positions[match.index+match[0].length-1];
      if(!start||!end||start[0].getRootNode()!==end[0].getRootNode())continue;
      const range=doc.createRange();range.setStart(start[0],start[1]);range.setEnd(end[0],end[1]+1);
      let visible=false;
      for(const rect of range.getClientRects()){
        if(rect.width<=0||rect.height<=0)continue;
        const overlay=doc.createElement('div');overlay.setAttribute('data-ezynotify-highlight',token);
        overlay.style.cssText='position:absolute!important;left:'+(rect.left+win.scrollX)+'px!important;top:'+(rect.top+win.scrollY)+'px!important;width:'+rect.width+'px!important;height:'+rect.height+'px!important;background:rgba(255,210,0,.38)!important;outline:2px solid #d79000!important;z-index:2147483647!important;pointer-events:none!important;box-sizing:border-box!important;';
        doc.documentElement.appendChild(overlay);visible=true;
      }
      if(visible)matches++;
    }
  }
  return matches;
}'''

CLEANUP = '''(target,token) => target.ownerDocument.querySelectorAll('[data-ezynotify-highlight]').forEach(el=>{if(el.getAttribute('data-ezynotify-highlight')===token)el.remove();})'''


async def capture_alert_images(page,target,content,config):
    if not config.get('screenshot'):return {}
    row=config.get('_monitor') or {}
    requests={}
    seen=row.get('keywords_seen') or []
    words=[str(k).casefold() for k in (row.get('keywords') or {}).get('keywords',[])]
    present=[k for k in words if k in content.casefold()]
    previous=row.get('keywords_matched',[])
    if any(k not in seen and (row.get('shouldContinueCheck',True) or k not in previous) for k in present):requests['keywords']=present
    old=row.get('reference') or ''
    if row.get('checkUpdates') and old and normalize(old).casefold()!=normalize(content).casefold():
        before=normalize(old).split();after=normalize(content).split()
        requests['updates']=[{'text':' '.join(after[c:d]),'before':' '.join(after[max(0,c-5):c]),'after':' '.join(after[d:d+5])}
            for tag,a,b,c,d in difflib.SequenceMatcher(None,before,after,autojunk=True).get_opcodes() if tag!='equal' and d>c]
    images={}
    for channel,phrases in requests.items():
        token=uuid.uuid4().hex
        try:
            async with asyncio.timeout(25):
                count=await target.evaluate(HIGHLIGHT,{'phrases':phrases,'ignored':config.get('ignore') or '', 'token':token})
                size=await page.evaluate('({width:Math.max(document.documentElement.scrollWidth,innerWidth),height:Math.max(document.documentElement.scrollHeight,innerHeight)})')
                # Never silently crop. Oversized pages retain their text alert.
                if size['width']*size['height']>60_000_000:continue
                data=await page.screenshot(type='jpeg',quality=85,full_page=True,timeout=15000,animations='disabled')
                if len(data)>9_000_000:continue
                note=f'Full page: {count} matching locations highlighted.' if count else 'Full page; removed text or an exact match could not be highlighted.'
                # Tall images are sent as files to preserve the complete page.
                document=size['width']+size['height']>10000 or max(size.values())/max(1,min(size.values()))>20
                images[channel]={'image':base64.b64encode(data).decode(),'note':note,'document':document,'matches':count}
        except Exception:
            pass  # Screenshot problems must not turn a successful check into a failure.
        finally:
            try:await target.evaluate(CLEANUP,token)
            except Exception:pass
    return images

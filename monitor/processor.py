from datetime import timedelta
import difflib
import html
import json
from .models import normalize, utcnow
from .health import process_health


def get_diff(old,new):
    """Word spans preserve additions, replacements and trailing deletions."""
    before=normalize(old).split();after=normalize(new).split()
    # autojunk keeps pathological/repetitive pages bounded; full input is capped.
    matcher=difflib.SequenceMatcher(None,before,after,autojunk=True)
    changes=[]
    for tag,a,b,c,d in matcher.get_opcodes():
        if tag!='equal':changes.append({'removed':' '.join(before[a:b]),'added':' '.join(after[c:d])})
    return changes


def alert(row,heading,text,channel='main'):
    title=html.escape((row.get('title') or 'Website monitor')[:60].replace('\u2014','-'))
    url=html.escape(row['url'],quote=True)
    # Escape AFTER truncating content so we never cut through an HTML entity/tag.
    kind='🌐 WEBSITE HEALTH' if channel=='health' or (row.get('fetch_config') or {}).get('mode')=='health' else '🔄 PAGE UPDATES' if channel=='updates' or (channel=='main' and row.get('checkUpdates')) else '🔎 KEYWORD WATCH'
    text=text.replace('\u2014','-')
    message=f'<b>{kind}</b>\n{heading}\n<b>{title}</b>\n\n{html.escape(text[:1900])}\n\n<a href="{url}">Open website ↗</a>'
    return {'channel':channel,'payload':{'text':message,'_checked_at':utcnow().isoformat(),'parse_mode':'HTML','link_preview_options':{'is_disabled':True},'reply_markup':{'inline_keyboard':[[{'text':'📋 My monitors','callback_data':'list:0'}]]}}}


async def process(row,result,ai,now=None):
    if (row.get('fetch_config') or {}).get('mode')=='health':return process_health(row,result,alert,now)
    now=now or utcnow()
    interval=row.get('interval_minutes') or 10
    patch={'last_status':result.status,'last_error':None,'failure_count':0,'next_check_at':(now+timedelta(minutes=interval)).isoformat(),'isUpdated':False}
    notifications=[]
    event=None
    old=row.get('reference') or ''
    channel='updates' if row.get('checkUpdates') else 'keywords'
    if result.status!='ok':
        failures=(row.get('failure_count') or 0)+1
        delay=max(interval*60,min(86400,60*2**min(failures,10)),result.retry_after)
        patch.update(failure_count=failures,last_error=result.reason,next_check_at=(now+timedelta(seconds=delay)).isoformat())
        # One alert per uninterrupted failure episode. Transient failures get
        # three chances before notifying; subsequent failures only back off.
        persistent=result.status in ('challenge','blocked','login','robots_denied','unsafe_url','unsupported','too_large')
        already_notified=bool(row.get('failure_notified'))
        if failures>=5:
            patch.update(paused=True,failure_notified=True)
            notice=alert(row,'⏸ <b>Monitor paused</b>',result.reason+' This monitor reached five consecutive failed checks. Automatic checks have stopped. Your last successful snapshot is preserved. Open My monitors, review the settings, then choose Resume to try again.',channel)
            notice['payload']['_auto_paused']=True
            notifications.append(notice)
        elif not already_notified and (persistent or failures>=3):
            if result.status=='challenge':
                message='The website requires human verification (CAPTCHA). EzyNotify could not access it. Your last successful snapshot is preserved; checks will retry less often.'
                heading='🛡 <b>Human verification required</b>'
            else:message=result.reason+' Your last successful snapshot is preserved. We’ll retry automatically.';heading='⚠️ <b>Check unavailable</b>'
            notifications.append(alert(row,heading,message,channel));patch['failure_notified']=True
        event={'status':result.status,'summary':result.reason,'details':{'http_status':result.http_status,'auto_paused':failures>=5}}
        return patch,event,notifications
    patch.update(last_success_at=now.isoformat(),reference=result.text,failure_notified=False)
    if row.get('failure_notified'):
        notifications.append(alert(row,'🟢 <b>Monitoring recovered</b>','The page is accessible again. Regular checks have resumed.',channel))
    words=[str(k).casefold() for k in (row.get('keywords') or {}).get('keywords',[])]
    present=[k for k in words if k in result.text.casefold()]
    seen=row.get('keywords_seen') or []
    new_matches=[k for k in present if k not in seen]
    patch['keywords_seen']=present
    if new_matches:
        found=(row.get('foundKeyword') or [])+[{'keyword':k,'foundAt':now.isoformat()} for k in new_matches]
        patch['foundKeyword']=found[-100:]
        notifications.append(alert(row,'🔎 <b>Keyword match</b>','Found: '+', '.join(new_matches),'keywords'))
    # Legacy snapshots were lowercased. Treat a case-only difference on the first
    # upgraded check as a baseline refresh, not a flood of migration alerts.
    changed=bool(old and normalize(old).casefold()!=normalize(result.text).casefold())
    if row.get('checkUpdates') and changed:
        changes=get_diff(old,result.text)
        summary,source=await ai.summarize(changes)
        message=summary
        if len(changes)>20 or any(len(c.get('added',''))>600 or len(c.get('removed',''))>600 for c in changes):message+='\n\nLarge update: this summary covers sampled change excerpts.'
        if row.get('shouldSendDetailedUpdates'):
            excerpts=[]
            for change in changes[:3]:
                if change['removed']:excerpts.append('− '+change['removed'][:160])
                if change['added']:excerpts.append('+ '+change['added'][:160])
            message+='\n\n'+'\n'.join(excerpts)
        if source=='fallback':message+='\n\nAI summary unavailable; showing detected text changes.'
        notifications.append(alert(row,'🔔 <b>Website changed</b>',message,'updates'))
        patch['isUpdated']=True
        patch['Updates']=((row.get('Updates') or [])+[{'summary':summary,'source':source,'time':now.isoformat()}])[-30:]
        event={'status':'changed','summary':summary,'details':{'source':source,'changes':[{'added':c['added'][:2000],'removed':c['removed'][:2000]} for c in changes[:20]]}}
    elif new_matches:event={'status':'matched','summary':'Found: '+', '.join(new_matches)}
    else:event={'status':'unchanged' if old else 'baseline','summary':'No change detected.' if old else 'Initial snapshot saved.'}
    if (new_matches or (row.get('checkUpdates') and changed)) and not row.get('shouldContinueCheck',True):patch['completed']=True
    return patch,event,notifications

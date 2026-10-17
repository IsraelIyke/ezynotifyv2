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
    changes=[]
    for tag,a,b,c,d in change_spans(before,after):
        if tag!='equal':changes.append({'removed':' '.join(before[a:b]),'added':' '.join(after[c:d])})
    return changes


def change_spans(before,after):
    """Keep full text; bound detailed diff work on very large replacements."""
    start=0;end_before=len(before);end_after=len(after)
    if max(end_before,end_after)>10_000:
        while start<min(end_before,end_after) and before[start]==after[start]:start+=1
        while end_before>start and end_after>start and before[end_before-1]==after[end_after-1]:
            end_before-=1;end_after-=1
    if max(end_before-start,end_after-start)>10_000:
        # A large changed section is represented in full, rather than dropping
        # content or allowing an unbounded word-alignment computation.
        return [('replace' if end_before>start and end_after>start else 'delete' if end_before>start else 'insert',start,end_before,start,end_after)]
    return [(tag,a+start,b+start,c+start,d+start) for tag,a,b,c,d in
            difflib.SequenceMatcher(None,before[start:end_before],after[start:end_after],autojunk=True).get_opcodes()]


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
        if failures==3:delay=max(0,result.retry_after)
        elif failures==4:delay=max(12*60*60,result.retry_after)
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
        elif failures==3:
            notifications.append(alert(row,'<b>Advanced search scheduled</b>','Three normal checks failed. We will try advanced search twice, 12 hours apart. Your last successful snapshot is preserved.',channel))
            patch['failure_notified']=True
        elif not already_notified and (persistent or failures>=3):
            if result.status=='challenge':
                message='The website requires human verification (CAPTCHA). EzyNotify could not access it. Your last successful snapshot is preserved; checks will retry less often.'
                heading='🛡 <b>Human verification required</b>'
            else:message=result.reason+' Your last successful snapshot is preserved. We’ll retry automatically.';heading='⚠️ <b>Check unavailable</b>'
            notifications.append(alert(row,heading,message,channel));patch['failure_notified']=True
        event={'status':result.status,'summary':result.reason,'details':{'http_status':result.http_status,'auto_paused':failures>=5,'search_stage':'advanced' if failures>3 else 'normal','advanced_pending':3<=failures<5}}
        return patch,event,notifications
    patch.update(last_success_at=now.isoformat(),reference=result.text,failure_notified=False)
    if row.get('failure_notified'):
        notifications.append(alert(row,'🟢 <b>Monitoring recovered</b>','The page is accessible again. Regular checks have resumed.',channel))
    words=[str(k).casefold() for k in (row.get('keywords') or {}).get('keywords',[])]
    present=[k for k in words if k in result.text.casefold()]
    seen=row.get('keywords_seen') or []
    new_matches=[k for k in present if k not in seen]
    previous_matches=row.get('keywords_matched',[item.get('keyword','').casefold() for item in (row.get('foundKeyword') or [])])
    if not row.get('shouldContinueCheck',True):new_matches=[k for k in new_matches if k not in previous_matches]
    matched=[k for k in words if k in previous_matches or k in present]
    patch['keywords_matched']=matched
    patch['keywords_seen']=present
    if new_matches:
        found=(row.get('foundKeyword') or [])+[{'keyword':k,'foundAt':now.isoformat()} for k in new_matches]
        patch['foundKeyword']=found[-100:]
        message='Found: '+', '.join(new_matches)
        if not row.get('shouldContinueCheck',True):
            remaining=[k for k in words if k not in matched]
            message+=f'\nProgress: {len(matched)} of {len(words)} entries found.'
            message+='\nStill watching for: '+', '.join(remaining) if remaining else '\nAll entries found. This monitor is now finished.'
        notifications.append(alert(row,'🔎 <b>Keyword match</b>',message,'keywords'))
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
    if not row.get('shouldContinueCheck',True):
        if row.get('checkUpdates'):
            if changed:patch['completed']=True
        elif words and all(k in matched for k in words):patch['completed']=True
    if (row.get('fetch_config') or {}).get('screenshot'):
        for kind,trigger in [('keywords',bool(new_matches)),('updates',bool(row.get('checkUpdates') and changed))]:
            if not trigger:continue
            captured=result.alert_images.get(kind)
            if captured:
                title=html.escape((row.get('title') or 'Website monitor')[:60])
                notifications.append({'channel':kind,'payload':{'text':f'<b>{title}</b>\n'+html.escape(captured['note']),
                    '_photo':captured['image'],'_document':captured.get('document',False),'_checked_at':now.isoformat(),'parse_mode':'HTML'}})
            else:
                for notice in notifications:
                    if notice['channel']==kind:notice['payload']['text']+='\n\nScreenshot unavailable for this result.'
    return patch,event,notifications

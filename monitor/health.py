"""Availability transitions, independent of page extraction and AI."""
from datetime import timedelta
from .models import utcnow


def process_health(row,result,alert,now=None):
    now=now or utcnow()
    interval=row.get('interval_minutes') or 10
    up=result.status=='ok'
    down=result.status in ('health_down','timeout','network_error','http_error')
    previous=row.get('reference')
    patch={'last_status':'ok' if up else 'health_down' if down else 'health_unknown',
           'last_error':None if up else result.reason,'failure_count':0 if up else (row.get('failure_count') or 0)+1,
           'failure_notified':False if up else True,
           'next_check_at':(now+timedelta(seconds=max(interval*60,result.retry_after))).isoformat(),'isUpdated':False}
    notices=[]
    if up:
        patch.update(reference='health:up',last_success_at=now.isoformat())
        if previous=='health:down':notices.append(alert(row,'🟢 <b>Website back online</b>','The website is responding successfully again. Health checks will continue.','health'))
    elif down:
        patch['reference']='health:down'
        if previous!='health:down':notices.append(alert(row,'🔴 <b>Website unavailable</b>',result.reason+' The website could not be reached successfully from our checker. We will keep checking and notify you when it recovers.','health'))
    elif not row.get('failure_notified'):
        notices.append(alert(row,'🟡 <b>Health check inconclusive</b>',result.reason+' This does not confirm the website is down. We will keep checking.','health'))
    event={'status':patch['last_status'],'summary':'Website is responding.' if up else result.reason,
           'details':{'http_status':result.http_status,'monitor_type':'health','state':'up' if up else 'down' if down else 'unknown'}}
    return patch,event,notices

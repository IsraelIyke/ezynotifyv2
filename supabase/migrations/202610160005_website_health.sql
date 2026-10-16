begin;
-- Health monitors must continue probing through outages to detect recovery.
create or replace function public.enforce_monitor_failure_limit() returns trigger
language plpgsql set search_path=public as $$
begin
  if old.paused and not new.paused then
    new.failure_count=0;
    new.failure_notified=false;
  elsif new.failure_count>=5 and not new.paused and coalesce(new.fetch_config->>'mode','browser')<>'health' then
    new.paused=true;
    new.lease_owner=null;
    new.lease_until=null;
  end if;
  return new;
end $$;

create or replace function public.enqueue_admin_monitor_failure() returns trigger
language plpgsql security definer set search_path=public as $$
declare m ezynotify; destination text; host text; message text;
begin
  if new.status in ('ok','baseline','unchanged','changed','matched') then return new; end if;
  select chat_id into destination from monitor_admin_settings where singleton and enabled;
  if destination is null then return new; end if;
  select * into m from ezynotify where id::text=new.monitor_id;
  if not found then return new; end if;
  host=coalesce(substring(m.url from '^https?://(?:[^/@]*@)?([^/?#]+)'),'(invalid URL)');
  message='🚨 <b>ADMIN: WEBSITE CHECK FAILED</b>'||E'\n\n<b>Website:</b> '||escape_alert_html(host)||
    E'\n<b>Monitor:</b> #'||escape_alert_html(new.monitor_id)||
    E'\n<b>Type:</b> '||case when m.fetch_config->>'mode'='health' then '🌐 Website health' when m."checkUpdates" then '🔄 Page updates' else '🔎 Keyword watch' end||
    E'\n<b>Status:</b> '||escape_alert_html(new.status)||
    E'\n<b>Reason:</b> '||escape_alert_html(left(coalesce(new.summary,'No reason provided.'),400))||
    E'\n<b>Consecutive failures:</b> '||m.failure_count::text||
    case when coalesce(new.details->>'http_status','0')<>'0' then E'\n<b>HTTP:</b> '||escape_alert_html(new.details->>'http_status') else '' end;
  insert into notification_outbox(monitor_id,chat_id,channel,payload)
    values(new.monitor_id,destination,'admin_failure',jsonb_build_object('text',message,'parse_mode','HTML',
      '_checked_at',new.created_at,'link_preview_options',jsonb_build_object('is_disabled',true)));
  return new;
end $$;
commit;

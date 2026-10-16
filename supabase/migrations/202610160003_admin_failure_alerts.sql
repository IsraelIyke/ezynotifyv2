begin;
create table if not exists public.monitor_admin_settings (
  singleton boolean primary key default true check(singleton),
  chat_id text not null check(chat_id ~ '^[0-9]+$'),
  enabled boolean not null default true
);
alter table public.monitor_admin_settings enable row level security;
revoke all on public.monitor_admin_settings from anon,authenticated;
grant select,insert,update,delete on public.monitor_admin_settings to service_role;

create or replace function public.escape_alert_html(value text) returns text
language sql immutable set search_path=public as $$
  select replace(replace(replace(coalesce(value,''),'&','&amp;'),'<','&lt;'),'>','&gt;');
$$;

create or replace function public.enqueue_admin_monitor_failure() returns trigger
language plpgsql security definer set search_path=public as $$
declare m ezynotify; destination text; host text; message text;
begin
  if new.status in ('ok','baseline','unchanged','changed','matched') then return new; end if;
  select chat_id into destination from monitor_admin_settings where singleton and enabled;
  if destination is null then return new; end if;
  select * into m from ezynotify where id::text=new.monitor_id;
  if not found then return new; end if;
  -- Only the hostname is disclosed. Credentials, URL paths, query parameters,
  -- page content and user profile data are excluded from admin messages.
  host=coalesce(substring(m.url from '^https?://(?:[^/@]*@)?([^/?#]+)'),'(invalid URL)');
  message='🚨 <b>ADMIN: WEBSITE CHECK FAILED</b>'||E'\n\n<b>Website:</b> '||escape_alert_html(host)||
    E'\n<b>Monitor:</b> #'||escape_alert_html(new.monitor_id)||
    E'\n<b>Type:</b> '||case when m."checkUpdates" then '🔄 Page updates' else '🔎 Keyword watch' end||
    E'\n<b>Status:</b> '||escape_alert_html(new.status)||
    E'\n<b>Reason:</b> '||escape_alert_html(left(coalesce(new.summary,'No reason provided.'),400))||
    E'\n<b>Consecutive failures:</b> '||m.failure_count::text||
    case when coalesce(new.details->>'http_status','0')<>'0' then E'\n<b>HTTP:</b> '||escape_alert_html(new.details->>'http_status') else '' end;
  insert into notification_outbox(monitor_id,chat_id,channel,payload)
    values(new.monitor_id,destination,'admin_failure',jsonb_build_object('text',message,'parse_mode','HTML',
      '_checked_at',new.created_at,'link_preview_options',jsonb_build_object('is_disabled',true)));
  return new;
end $$;
drop trigger if exists admin_monitor_failure on public.monitor_events;
create trigger admin_monitor_failure after insert on public.monitor_events
  for each row execute function public.enqueue_admin_monitor_failure();

-- Separate claims let the Supabase sender deliver admin alerts independently
-- of browser jobs and without applying end-user failure suppression.
create or replace function public.claim_admin_notifications(p_owner text,p_limit integer default 20)
returns setof public.notification_outbox language sql security definer set search_path=public as $$
  update notification_outbox n set lease_owner=p_owner,lease_until=now()+interval '2 minutes'
  where id in (select id from notification_outbox where state='pending' and channel='admin_failure'
    and next_attempt_at<=now() and (lease_until is null or lease_until<now())
    order by id for update skip locked limit least(greatest(p_limit,1),20)) returning n.*;
$$;
create or replace function public.claim_notifications(p_owner text,p_limit integer default 100)
returns setof public.notification_outbox language sql security definer set search_path=public as $$
  update notification_outbox n set lease_owner=p_owner,lease_until=now()+interval '2 minutes'
  where id in (select id from notification_outbox where state='pending' and channel<>'admin_failure' and next_attempt_at<=now()
    and (lease_until is null or lease_until<now()) order by id for update skip locked limit least(p_limit,200)) returning n.*;
$$;
revoke all on function public.claim_admin_notifications(text,integer),public.enqueue_admin_monitor_failure() from public,anon,authenticated;
grant execute on function public.claim_admin_notifications(text,integer) to service_role;

create table if not exists public.privacy_requests (
  id bigint generated always as identity primary key,
  chat_id text not null,
  kind text not null check(kind in ('access','correction','deletion','other')),
  status text not null default 'pending' check(status in ('pending','in_review','completed')),
  created_at timestamptz not null default now()
);
create unique index if not exists privacy_requests_open_idx on public.privacy_requests(chat_id,kind) where status='pending';
alter table public.privacy_requests enable row level security;
revoke all on public.privacy_requests from anon,authenticated;
grant select,insert,update,delete on public.privacy_requests to service_role;
grant usage,select on sequence public.privacy_requests_id_seq to service_role;
create or replace function public.request_bot_privacy(p_chat text,p_kind text) returns bigint
language plpgsql security definer set search_path=public as $$
declare request_id bigint;
begin
  insert into privacy_requests(chat_id,kind) values(p_chat,p_kind)
  on conflict(chat_id,kind) where status='pending' do update set kind=excluded.kind
  returning id into request_id;
  return request_id;
end $$;
revoke all on function public.request_bot_privacy(text,text) from public,anon,authenticated;
grant execute on function public.request_bot_privacy(text,text) to service_role;
commit;

-- Run after schema.sql on fresh projects. Existing ezynotify rows are retained.
begin;
-- The original live table uses json (which has no equality operator). jsonb
-- preserves the data while enabling atomic comparisons and consistent defaults.
alter table public.ezynotify
  alter column keywords type jsonb using keywords::jsonb,
  alter column "foundKeyword" type jsonb using "foundKeyword"::jsonb,
  alter column "Updates" type jsonb using "Updates"::jsonb;
update public.ezynotify set reference=coalesce(reference,''),
  "foundKeyword"=coalesce("foundKeyword",'[]'::jsonb), "Updates"=coalesce("Updates",'[]'::jsonb),
  completed=coalesce(completed,false),"checkUpdates"=coalesce("checkUpdates",false);
alter table public.ezynotify
  add column if not exists title text not null default '',
  add column if not exists paused boolean not null default false,
  add column if not exists interval_minutes integer not null default 10 check (interval_minutes between 10 and 10080),
  add column if not exists fetch_config jsonb not null default '{"mode":"browser"}'::jsonb,
  add column if not exists next_check_at timestamptz not null default now(),
  add column if not exists last_checked_at timestamptz,
  add column if not exists last_success_at timestamptz,
  add column if not exists last_manual_at timestamptz,
  add column if not exists last_status text not null default 'pending',
  add column if not exists last_error text,
  add column if not exists failure_count integer not null default 0,
  add column if not exists failure_notified boolean not null default false,
  add column if not exists row_version bigint not null default 0,
  add column if not exists lease_owner text,
  add column if not exists lease_until timestamptz,
  add column if not exists keywords_seen jsonb not null default '[]'::jsonb;

create index if not exists ezynotify_due_idx on public.ezynotify(next_check_at) where not paused and not completed;
create index if not exists ezynotify_owner_idx on public.ezynotify("telegramID", created_at desc);

create table if not exists public.bot_sessions (
  chat_id text primary key,
  draft jsonb not null,
  expires_at timestamptz not null default now() + interval '30 minutes'
);
create table if not exists public.bot_updates (
  update_id bigint primary key,
  state text not null default 'processing',
  expires_at timestamptz not null default now() + interval '2 minutes',
  created_at timestamptz not null default now()
);
create table if not exists public.runtime_locks (
  name text primary key,
  owner text not null,
  expires_at timestamptz not null
);
create table if not exists public.fetch_cache (
  cache_key text primary key,
  result jsonb not null,
  fetched_at timestamptz not null default now()
);
create table if not exists public.monitor_events (
  id bigint generated always as identity primary key,
  monitor_id text not null,
  status text not null,
  summary text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists monitor_events_monitor_idx on public.monitor_events(monitor_id, created_at desc);
create table if not exists public.notification_outbox (
  id bigint generated always as identity primary key,
  monitor_id text not null,
  chat_id text not null,
  channel text not null default 'main',
  payload jsonb not null,
  state text not null default 'pending',
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  lease_owner text,
  lease_until timestamptz,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  last_error text
);
create index if not exists notification_outbox_due_idx on public.notification_outbox(next_attempt_at) where state = 'pending';

-- Never expose internal state through public API keys.
alter table public.ezynotify enable row level security;
revoke all on public.ezynotify from anon, authenticated;
alter table public.bot_sessions enable row level security;
alter table public.bot_updates enable row level security;
alter table public.runtime_locks enable row level security;
alter table public.fetch_cache enable row level security;
alter table public.monitor_events enable row level security;
alter table public.notification_outbox enable row level security;
revoke all on public.bot_sessions, public.bot_updates, public.runtime_locks, public.fetch_cache, public.monitor_events, public.notification_outbox from anon, authenticated;
grant all on public.ezynotify, public.bot_sessions, public.bot_updates, public.runtime_locks, public.fetch_cache, public.monitor_events, public.notification_outbox to service_role;
grant usage, select on sequence public.ezynotify_id_seq,public.monitor_events_id_seq,public.notification_outbox_id_seq to service_role;

create or replace function public.acquire_runtime_lock(p_name text, p_owner text, p_seconds integer default 120)
returns boolean language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  insert into runtime_locks(name,owner,expires_at) values(p_name,p_owner,now()+make_interval(secs=>least(p_seconds,3600)))
  on conflict(name) do update set owner=excluded.owner, expires_at=excluded.expires_at
  where runtime_locks.expires_at < now() or runtime_locks.owner=p_owner;
  get diagnostics n = row_count;
  return n=1;
end $$;

create or replace function public.claim_bot_update(p_id bigint)
returns boolean language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  insert into bot_updates(update_id) values(p_id)
  on conflict(update_id) do update set expires_at=now()+interval '2 minutes'
  where bot_updates.state='processing' and bot_updates.expires_at < now();
  get diagnostics n = row_count;
  return n=1;
end $$;

create or replace function public.commit_bot_draft(p_chat text,p_nonce text,p_fields jsonb,p_id text default null)
returns text language plpgsql security definer set search_path=public as $$
declare s bot_sessions; saved_id text;
begin
  select * into s from bot_sessions where chat_id=p_chat for update;
  if not found or s.expires_at<now() or s.draft->>'nonce'<>p_nonce or s.draft->>'step'<>'review' then raise exception 'Draft expired'; end if;
  if p_id is not null then
    update ezynotify set url=p_fields->>'url',title=p_fields->>'title',
      "checkUpdates"=(p_fields->>'checkUpdates')::boolean, keywords=nullif(p_fields->'keywords','null'::jsonb),
      interval_minutes=(p_fields->>'interval_minutes')::integer,
      "shouldContinueCheck"=(p_fields->>'shouldContinueCheck')::boolean,
      "shouldSendDetailedUpdates"=(p_fields->>'shouldSendDetailedUpdates')::boolean,
      fetch_config=p_fields->'fetch_config',completed=false,next_check_at=now(),
      reference=case when p_fields?'reference' then p_fields->>'reference' else reference end,
      keywords_seen=coalesce(p_fields->'keywords_seen',keywords_seen)
      where id::text=p_id and "telegramID"::text=p_chat returning id::text into saved_id;
    if saved_id is null then raise exception 'Monitor not owned by chat'; end if;
  else
    if (select count(*) from ezynotify where "telegramID"::text=p_chat)>=50 then raise exception 'Monitor limit reached'; end if;
    -- jsonb_populate_record handles either the legacy bigint or new text chat ID.
    insert into ezynotify(url,title,"telegramID","checkUpdates",keywords,interval_minutes,"shouldContinueCheck","shouldSendDetailedUpdates",fetch_config)
      select p_fields->>'url',p_fields->>'title',
      (jsonb_populate_record(null::ezynotify,jsonb_build_object('telegramID',p_chat)))."telegramID",
      (p_fields->>'checkUpdates')::boolean,nullif(p_fields->'keywords','null'::jsonb),
      (p_fields->>'interval_minutes')::integer,(p_fields->>'shouldContinueCheck')::boolean,
      (p_fields->>'shouldSendDetailedUpdates')::boolean,p_fields->'fetch_config'
      returning id::text into saved_id;
  end if;
  delete from bot_sessions where chat_id=p_chat;
  return saved_id;
end $$;

create or replace function public.claim_monitors(p_owner text, p_limit integer default 80, p_monitor_id text default null)
returns setof public.ezynotify language sql security definer set search_path = public as $$
  update ezynotify m set lease_owner=p_owner,lease_until=now()+interval '15 minutes'
  where m.id in (
    select id from ezynotify where not paused and not completed and next_check_at<=now()
    and (lease_until is null or lease_until<now())
    and (p_monitor_id is null or id::text=p_monitor_id)
    order by next_check_at for update skip locked limit least(p_limit,200)
  ) returning m.*;
$$;

create or replace function public.request_monitor_check(p_id text,p_chat text)
returns text language plpgsql security definer set search_path=public as $$
declare m ezynotify;
begin
  select * into m from ezynotify where id::text=p_id and "telegramID"::text=p_chat for update;
  if not found then return 'missing'; end if;
  if m.paused or m.completed then return 'inactive'; end if;
  if m.last_manual_at > now()-interval '2 minutes' then return 'cooldown'; end if;
  update ezynotify set next_check_at=now(), last_manual_at=now() where id=m.id;
  return 'queued';
end $$;

-- Result, baseline, history and notification intent commit together. A stale worker
-- cannot overwrite a user edit or another worker's result.
create or replace function public.finish_monitor(p_id text,p_owner text,p_version bigint,p_patch jsonb,p_event jsonb,p_notifications jsonb)
returns boolean language plpgsql security definer set search_path=public as $$
declare m ezynotify; item jsonb;
begin
  select * into m from ezynotify where id::text=p_id for update;
  if not found or m.lease_owner is distinct from p_owner or m.row_version<>p_version or m.lease_until is null or m.lease_until<now() or m.paused then return false; end if;
  update ezynotify set
    reference=coalesce(p_patch->>'reference',reference),
    "foundKeyword"=coalesce(p_patch->'foundKeyword',"foundKeyword"),
    keywords_seen=coalesce(p_patch->'keywords_seen',keywords_seen),
    "Updates"=coalesce(p_patch->'Updates',"Updates"),
    "isUpdated"=coalesce((p_patch->>'isUpdated')::boolean,"isUpdated"),
    completed=coalesce((p_patch->>'completed')::boolean,completed),
    last_checked_at=now(),
    last_success_at=coalesce((p_patch->>'last_success_at')::timestamptz,last_success_at),
    last_status=p_patch->>'last_status', last_error=p_patch->>'last_error',
    failure_count=coalesce((p_patch->>'failure_count')::integer,failure_count),
    failure_notified=coalesce((p_patch->>'failure_notified')::boolean,failure_notified),
    next_check_at=(p_patch->>'next_check_at')::timestamptz,
    lease_owner=null,lease_until=null,row_version=row_version+1
    where id=m.id;
  if p_event is not null then
    insert into monitor_events(monitor_id,status,summary,details)
    values(p_id,p_event->>'status',p_event->>'summary',coalesce(p_event->'details','{}'::jsonb));
  end if;
  for item in select value from jsonb_array_elements(coalesce(p_notifications,'[]'::jsonb)) loop
    insert into notification_outbox(monitor_id,chat_id,channel,payload)
    values(p_id,m."telegramID"::text,coalesce(item->>'channel','main'),item->'payload');
  end loop;
  return true;
end $$;

create or replace function public.claim_notifications(p_owner text,p_limit integer default 100)
returns setof public.notification_outbox language sql security definer set search_path=public as $$
  update notification_outbox n set lease_owner=p_owner,lease_until=now()+interval '2 minutes'
  where id in (select id from notification_outbox where state='pending' and next_attempt_at<=now()
    and (lease_until is null or lease_until<now()) order by id for update skip locked limit least(p_limit,200)) returning n.*;
$$;

-- Any edit invalidates the in-flight version; an explicit worker finish increments
-- the version too, so no generic trigger is needed on worker-owned columns.
create or replace function public.invalidate_monitor_edit() returns trigger language plpgsql set search_path=public as $$
begin
  if (new.url,new.keywords,new.fetch_config,new.paused,new."shouldContinueCheck",new.interval_minutes,new.title,new."shouldSendDetailedUpdates",new.completed)
    is distinct from (old.url,old.keywords,old.fetch_config,old.paused,old."shouldContinueCheck",old.interval_minutes,old.title,old."shouldSendDetailedUpdates",old.completed)
    and new.row_version=old.row_version then
    new.row_version=old.row_version+1; new.lease_owner=null; new.lease_until=null;
  end if;
  return new;
end $$;
drop trigger if exists invalidate_monitor_edit on public.ezynotify;
create trigger invalidate_monitor_edit before update on public.ezynotify for each row execute function public.invalidate_monitor_edit();

-- Functions are callable only by trusted services; chat ownership is checked by
-- the webhook, and manual-check RPC also enforces it within the transaction.
revoke all on function public.acquire_runtime_lock(text,text,integer), public.claim_bot_update(bigint), public.claim_monitors(text,integer,text), public.request_monitor_check(text,text), public.finish_monitor(text,text,bigint,jsonb,jsonb,jsonb), public.claim_notifications(text,integer) from public,anon,authenticated;
grant execute on function public.acquire_runtime_lock(text,text,integer), public.claim_bot_update(bigint), public.claim_monitors(text,integer,text), public.request_monitor_check(text,text), public.finish_monitor(text,text,bigint,jsonb,jsonb,jsonb), public.claim_notifications(text,integer) to service_role;
revoke all on function public.commit_bot_draft(text,text,jsonb,text) from public,anon,authenticated;
grant execute on function public.commit_bot_draft(text,text,jsonb,text) to service_role;
commit;

begin;
alter table public.privacy_requests
  add column if not exists active_erased_at timestamptz,
  add column if not exists external_review_note text,
  add column if not exists resolution text,
  add column if not exists completed_at timestamptz,
  add column if not exists erase_token uuid,
  add column if not exists erase_expires_at timestamptz;

create table public.privacy_admin_settings (
  singleton boolean primary key default true check(singleton),
  enabled boolean not null default false,
  chat_id text not null,
  admin_user_id text not null
);
create table public.privacy_audit (
  id bigint generated always as identity primary key,
  request_id bigint not null references public.privacy_requests(id) on delete cascade,
  action text not null,actor text not null,details jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create table public.privacy_outbox (
  id bigint generated always as identity primary key,
  request_id bigint not null references public.privacy_requests(id) on delete cascade,
  destination text not null check(destination in ('admin','user')),
  chat_id text not null,payload jsonb not null,dedupe_key text unique,
  state text not null default 'pending' check(state in ('pending','sent','failed')),
  attempts integer not null default 0,next_attempt_at timestamptz not null default now(),
  lease_owner text,lease_until timestamptz,created_at timestamptz not null default now(),sent_at timestamptz
);
create table public.privacy_admin_sessions (
  admin_id text primary key,request_id bigint not null references public.privacy_requests(id) on delete cascade,
  action text not null,draft jsonb not null default '{}',expires_at timestamptz not null default now()+interval '30 minutes'
);
create table public.privacy_admin_updates (
  update_id bigint primary key,state text not null default 'processing',
  expires_at timestamptz not null default now()+interval '2 minutes',created_at timestamptz not null default now()
);
alter table public.privacy_admin_settings enable row level security;
alter table public.privacy_audit enable row level security;
alter table public.privacy_outbox enable row level security;
alter table public.privacy_admin_sessions enable row level security;
alter table public.privacy_admin_updates enable row level security;
revoke all on public.privacy_admin_settings,public.privacy_audit,public.privacy_outbox,public.privacy_admin_sessions,public.privacy_admin_updates from public,anon,authenticated;
grant all on public.privacy_admin_settings,public.privacy_audit,public.privacy_outbox,public.privacy_admin_sessions,public.privacy_admin_updates to service_role;
grant usage,select on sequence public.privacy_audit_id_seq,public.privacy_outbox_id_seq to service_role;

create function public.privacy_admin_allowed(p_actor text) returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from privacy_admin_settings where singleton and enabled and admin_user_id=p_actor);
$$;

create function public.queue_privacy_request() returns trigger
language plpgsql security definer set search_path=public as $$
declare destination text;
begin
  select chat_id into destination from privacy_admin_settings where singleton and enabled;
  if destination is not null then
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
    values(new.id,'admin',destination,jsonb_build_object(
      'text','<b>Privacy request #'||new.id::text||'</b>'||E'\nType: '||new.kind||E'\nAccount: '||escape_alert_html(new.chat_id)||
      E'\nReceived: '||new.created_at::text||E'\n\nOpen the request to review it or preview an action.',
      'parse_mode','HTML','reply_markup',jsonb_build_object('inline_keyboard',jsonb_build_array(jsonb_build_array(
        jsonb_build_object('text','Open request','callback_data','pv:view:'||new.id::text))))),
      'request:'||new.id::text) on conflict(dedupe_key) do nothing;
  end if;
  return new;
end $$;
create trigger queue_privacy_request after insert on public.privacy_requests for each row execute function public.queue_privacy_request();

create or replace function public.request_bot_privacy(p_chat text,p_kind text) returns bigint
language plpgsql security definer set search_path=public as $$
declare request_id bigint;
begin
  select id into request_id from privacy_requests where chat_id=p_chat and kind=p_kind and status in ('pending','in_review') order by id limit 1;
  if request_id is not null then return request_id; end if;
  insert into privacy_requests(chat_id,kind) values(p_chat,p_kind)
  on conflict(chat_id,kind) where status='pending' do update set kind=excluded.kind returning id into request_id;
  return request_id;
end $$;

create function public.claim_privacy_notifications(p_owner text,p_limit integer default 20)
returns setof public.privacy_outbox language sql security definer set search_path=public as $$
  update privacy_outbox o set lease_owner=p_owner,lease_until=now()+interval '2 minutes'
  where id in (select id from privacy_outbox where state='pending' and next_attempt_at<=now()
    and (lease_until is null or lease_until<now()) order by id for update skip locked limit least(p_limit,20)) returning o.*;
$$;
create function public.claim_privacy_update(p_id bigint) returns boolean
language plpgsql security definer set search_path=public as $$
declare n integer;
begin
  insert into privacy_admin_updates(update_id) values(p_id)
  on conflict(update_id) do update set expires_at=now()+interval '2 minutes'
  where privacy_admin_updates.state='processing' and privacy_admin_updates.expires_at<now();
  get diagnostics n=row_count;return n=1;
end $$;

create function public.privacy_action(p_id bigint,p_actor text,p_action text,p_note text default '',p_token text default '',p_event text default '') returns jsonb
language plpgsql security definer set search_path=public as $$
declare r privacy_requests; ids text[]; request_ids bigint[]; counts jsonb; token uuid; lock_owner text; n integer; changes jsonb; outcome text;
begin
  if not privacy_admin_allowed(p_actor) then raise exception 'Not authorized'; end if;
  select * into r from privacy_requests where id=p_id for update;
  if not found then return jsonb_build_object('error','missing'); end if;
  if p_action='retry_delivery' then
    update privacy_outbox set state='pending',attempts=0,next_attempt_at=now(),lease_owner=null,lease_until=null
      where request_id=p_id and state='failed';
    get diagnostics n=row_count;return jsonb_build_object('requeued',n);
  end if;
  if r.status='completed' then return jsonb_build_object('error','closed'); end if;
  if length(p_note)>2000 then return jsonb_build_object('error','note_too_long'); end if;
  if p_action in ('erase','complete') then
    perform 1 from privacy_outbox where request_id in (select id from privacy_requests where chat_id=r.chat_id) order by id for update;
    if exists(select 1 from privacy_outbox where request_id in (select id from privacy_requests where chat_id=r.chat_id)
      and state='pending' and lease_until>now()) then return jsonb_build_object('error','privacy_delivery_busy'); end if;
  end if;
  if p_action='review' then
    if r.status='pending' then
      update privacy_requests set status='in_review' where id=p_id;
      insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
        values(p_id,'user',r.chat_id,jsonb_build_object('text','Your privacy request #'||p_id::text||' is now being handled by the operator.'),'review:'||p_id::text) on conflict do nothing;
    end if;
  elsif p_action='reply' then
    if length(trim(p_note))<3 then return jsonb_build_object('error','note_required'); end if;
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
      values(p_id,'user',r.chat_id,jsonb_build_object('text','Privacy request #'||p_id::text||E'\n\n'||p_note||E'\n\nTo reply, send /privacy_note '||p_id::text||' followed by your message.'),case when p_event<>'' then 'reply:'||p_id::text||':'||p_event else null end) on conflict do nothing;
  elsif p_action='retention' then
    if length(trim(p_note))<15 then return jsonb_build_object('error','note_required'); end if;
    update privacy_requests set external_review_note=p_note where id=p_id;
  elsif p_action='correct' then
    if r.kind<>'correction' then return jsonb_build_object('error','wrong_kind'); end if;
    changes=p_note::jsonb;
    if jsonb_typeof(changes)<>'object' or changes='{}'::jsonb or exists(select 1 from jsonb_object_keys(changes) k where k not in ('first_name','last_name','username','language_code','timezone')) then return jsonb_build_object('error','invalid_fields'); end if;
    if exists(select 1 from jsonb_each(changes) item where jsonb_typeof(item.value)<>'string' or length(item.value #>> '{}')>100) then return jsonb_build_object('error','invalid_fields'); end if;
    if changes?'timezone' and not exists(select 1 from pg_timezone_names where name=changes->>'timezone') then return jsonb_build_object('error','invalid_timezone'); end if;
    if changes-'timezone'<>'{}'::jsonb then
      update bot_users set first_name=case when changes?'first_name' then changes->>'first_name' else first_name end,
        last_name=case when changes?'last_name' then changes->>'last_name' else last_name end,
        username=case when changes?'username' then changes->>'username' else username end,
        language_code=case when changes?'language_code' then changes->>'language_code' else language_code end where chat_id=r.chat_id;
      if not found then return jsonb_build_object('error','profile_missing'); end if;
    end if;
    if changes?'timezone' then
      insert into bot_preferences(chat_id,timezone) values(r.chat_id,changes->>'timezone')
        on conflict(chat_id) do update set timezone=excluded.timezone,updated_at=now();
    end if;
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
      values(p_id,'user',r.chat_id,jsonb_build_object('text','The operator applied the requested app-profile/preferences correction for privacy request #'||p_id::text||'. Telegram account details must be changed in Telegram itself.'),case when p_event<>'' then 'correct:'||p_id::text||':'||p_event else null end) on conflict do nothing;
  elsif p_action='export' then
    if r.kind<>'access' then return jsonb_build_object('error','wrong_kind'); end if;
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
      values(p_id,'user',r.chat_id,jsonb_build_object('text','Account/settings export for privacy request #'||p_id::text||'. This file excludes page snapshots, screenshots and historical delivery/check records; the operator must review those separately if relevant to your request.',
        '_document_json',jsonb_build_object('scope','Account profile, preferences and monitor settings only',
          'profile',(select to_jsonb(u) from bot_users u where u.chat_id=r.chat_id),
          'preferences',(select to_jsonb(p) from bot_preferences p where p.chat_id=r.chat_id),
          'monitors',coalesce((select jsonb_agg(jsonb_build_object('id',m.id,'url',m.url,'title',m.title,'keywords',m.keywords,'fetch_config',m.fetch_config,'interval_minutes',m.interval_minutes,'check_updates',m."checkUpdates",'keep_watching',m."shouldContinueCheck",'paused',m.paused,'completed',m.completed)) from ezynotify m where m."telegramID"::text=r.chat_id),'[]'::jsonb))),case when p_event<>'' then 'export:'||p_id::text||':'||p_event else null end) on conflict do nothing;
  elsif p_action in ('preview','erase') then
    if r.kind<>'deletion' then return jsonb_build_object('error','not_deletion'); end if;
    if r.active_erased_at is not null and not exists(select 1 from bot_users where chat_id=r.chat_id)
      and not exists(select 1 from ezynotify where "telegramID"::text=r.chat_id)
      and not exists(select 1 from bot_sessions where chat_id=r.chat_id)
      and not exists(select 1 from bot_preferences where chat_id=r.chat_id) then return jsonb_build_object('error','already_erased'); end if;
    select coalesce(array_agg(id::text),'{}') into ids from ezynotify where "telegramID"::text=r.chat_id;
    counts=jsonb_build_object('monitors',cardinality(ids),
      'profile',(select count(*) from bot_users where chat_id=r.chat_id),
      'preferences',(select count(*) from bot_preferences where chat_id=r.chat_id),
      'drafts',(select count(*) from bot_sessions where chat_id=r.chat_id),
      'events',(select count(*) from monitor_events where monitor_id=any(ids)),
      'notifications',(select count(*) from notification_outbox where monitor_id=any(ids) or chat_id=r.chat_id),
      'other_open_requests',(select count(*) from privacy_requests where chat_id=r.chat_id and id<>p_id and status<>'completed'),
      'shared_cache',(select count(*) from fetch_cache));
    if p_action='preview' then
      token=gen_random_uuid();
      update privacy_requests set erase_token=token,erase_expires_at=now()+interval '10 minutes' where id=p_id;
      return jsonb_build_object('counts',counts,'token',token,'account',r.chat_id);
    end if;
    if r.erase_token is null or r.erase_token::text<>p_token or r.erase_expires_at<now() then return jsonb_build_object('error','expired_confirmation'); end if;
    if exists(select 1 from privacy_requests where chat_id=r.chat_id and kind='access' and status<>'completed') then return jsonb_build_object('error','access_pending'); end if;
    -- Exclude both normal notification delivery and concurrent user edits.
    lock_owner='privacy-erase:'||gen_random_uuid()::text;
    insert into runtime_locks(name,owner,expires_at) values('worker',lock_owner,now()+interval '2 minutes')
      on conflict(name) do update set owner=excluded.owner,expires_at=excluded.expires_at where runtime_locks.expires_at<now();
    get diagnostics n=row_count;
    if n=0 then return jsonb_build_object('error','worker_busy'); end if;
    insert into runtime_locks(name,owner,expires_at) values('chat:'||r.chat_id,lock_owner,now()+interval '2 minutes')
      on conflict(name) do update set owner=excluded.owner,expires_at=excluded.expires_at where runtime_locks.expires_at<now();
    get diagnostics n=row_count;
    if n=0 then delete from runtime_locks where runtime_locks.owner=lock_owner;return jsonb_build_object('error','chat_busy'); end if;
    delete from notification_outbox where monitor_id=any(ids) or chat_id=r.chat_id;
    delete from monitor_events where monitor_id=any(ids);
    delete from ezynotify where id::text=any(ids);
    delete from bot_sessions where chat_id=r.chat_id;
    delete from bot_preferences where chat_id=r.chat_id;
    delete from bot_users where chat_id=r.chat_id;
    -- Cached snapshots are shared and not reliably attributable by owner.
    delete from fetch_cache;
    delete from privacy_outbox where request_id in (select id from privacy_requests where chat_id=r.chat_id);
    update privacy_requests set active_erased_at=now(),external_review_note=null,status='in_review',erase_token=null,erase_expires_at=null where id=p_id;
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
      values(p_id,'user',r.chat_id,jsonb_build_object('text','The active account data and monitors covered by privacy request #'||p_id::text||' have been erased. Backup/provider handling and a minimal request record remain under review. We will confirm the final outcome separately.'),'erased:'||p_id::text);
    insert into privacy_audit(request_id,actor,action,details) values(p_id,p_actor,'active_erased',counts);
    delete from runtime_locks where runtime_locks.owner=lock_owner;
    return jsonb_build_object('erased',true,'counts',counts);
  elsif p_action in ('complete','decline') then
    if length(trim(p_note))<15 then return jsonb_build_object('error','note_required'); end if;
    if p_action='complete' and r.kind='deletion' and (r.active_erased_at is null or r.external_review_note is null) then
      return jsonb_build_object('error','external_review_required');
    end if;
    if p_action='decline' and r.active_erased_at is not null then return jsonb_build_object('error','already_erased'); end if;
    if p_action='complete' and r.active_erased_at is not null and (exists(select 1 from bot_users where chat_id=r.chat_id) or exists(select 1 from ezynotify where "telegramID"::text=r.chat_id) or exists(select 1 from bot_sessions where chat_id=r.chat_id) or exists(select 1 from bot_preferences where chat_id=r.chat_id)) then return jsonb_build_object('error','new_activity'); end if;
    outcome='Privacy request #'||p_id::text||E'\n\n'||p_note||
      case when p_action='complete' and r.kind='deletion' then E'\n\nBackup/provider handling: '||r.external_review_note||E'\n\nA minimal, restricted request record is retained for up to 90 days. Existing Telegram messages are subject to Telegram account controls and retention.' else '' end;
    if length(outcome)>4000 then return jsonb_build_object('error','outcome_too_long'); end if;
    update privacy_requests set status='completed',completed_at=now(),resolution=p_action,
      erase_token=null,erase_expires_at=null where id=p_id;
    insert into privacy_outbox(request_id,destination,chat_id,payload,dedupe_key)
      values(p_id,'user',r.chat_id,jsonb_build_object('text',outcome,
        '_erase_receipt',p_action='complete' and r.kind='deletion'),'closed:'||p_id::text);
    if r.active_erased_at is not null and p_action='complete' then
      delete from privacy_outbox where request_id=p_id and dedupe_key is distinct from 'closed:'||p_id::text;
      select array_agg(id) into request_ids from privacy_requests where chat_id=r.chat_id;
      update privacy_requests set chat_id='erased:'||gen_random_uuid()::text,external_review_note=null
        where id=any(request_ids);
      update privacy_requests set status='completed',completed_at=coalesce(completed_at,now()),resolution=coalesce(resolution,'closed_by_erasure')
        where id=any(request_ids) and id<>p_id and status<>'completed';
    end if;
  else return jsonb_build_object('error','invalid_action');
  end if;
  insert into privacy_audit(request_id,actor,action) values(p_id,p_actor,p_action);
  return jsonb_build_object('ok',true);
end $$;

create function public.privacy_user_note(p_id bigint,p_chat text,p_note text) returns boolean
language plpgsql security definer set search_path=public as $$
declare destination text; r privacy_requests;
begin
  if length(trim(p_note))<1 or length(p_note)>2000 then return false; end if;
  select * into r from privacy_requests where id=p_id and chat_id=p_chat for update;
  if not found or r.status='completed' then return false; end if;
  select chat_id into destination from privacy_admin_settings where singleton and enabled;
  if destination is null then return false; end if;
  insert into privacy_outbox(request_id,destination,chat_id,payload)
    values(p_id,'admin',destination,jsonb_build_object('text','<b>Reply to privacy request #'||p_id::text||'</b>'||E'\n\n'||escape_alert_html(p_note),
      'parse_mode','HTML','reply_markup',jsonb_build_object('inline_keyboard',jsonb_build_array(jsonb_build_array(jsonb_build_object('text','Open request','callback_data','pv:view:'||p_id::text))))));
  return true;
end $$;

revoke all on function public.privacy_admin_allowed(text),public.queue_privacy_request(),public.claim_privacy_notifications(text,integer),public.claim_privacy_update(bigint),public.privacy_action(bigint,text,text,text,text,text),public.privacy_user_note(bigint,text,text) from public,anon,authenticated;
grant execute on function public.privacy_admin_allowed(text),public.claim_privacy_notifications(text,integer),public.claim_privacy_update(bigint),public.privacy_action(bigint,text,text,text,text,text),public.privacy_user_note(bigint,text,text) to service_role;
commit;

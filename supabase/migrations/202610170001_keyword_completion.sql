begin;
alter table public.ezynotify add column if not exists keywords_matched jsonb not null default '[]'::jsonb;
-- Seed durable progress from the actual historical matches, never guesses.
update public.ezynotify m set keywords_matched=coalesce((
  select jsonb_agg(distinct lower(k)) from jsonb_array_elements_text(coalesce(m.keywords->'keywords','[]'::jsonb)) k
  where exists (select 1 from jsonb_array_elements(coalesce(m."foundKeyword",'[]'::jsonb)) f where lower(f->>'keyword')=lower(k))
),'[]'::jsonb);

create or replace function public.enforce_keyword_completion() returns trigger
language plpgsql set search_path=public as $$
begin
  if old.keywords is distinct from new.keywords or old.url is distinct from new.url then
    new.keywords_matched='[]'::jsonb;
  end if;
  if new.completed and not coalesce(new."checkUpdates",false)
     and not coalesce(new."shouldContinueCheck",true)
     and coalesce(new.fetch_config->>'mode','browser')<>'health'
     and exists (select 1 from jsonb_array_elements_text(coalesce(new.keywords->'keywords','[]'::jsonb)) k
                 where not new.keywords_matched ? lower(k)) then
    new.completed=false;
  end if;
  return new;
end $$;
create trigger enforce_keyword_completion before update on public.ezynotify
  for each row execute function public.enforce_keyword_completion();
revoke all on function public.enforce_keyword_completion() from public,anon,authenticated;

-- Reactivate only prematurely finished one-shot keyword monitors.
update public.ezynotify m set completed=false,next_check_at=now(),
  lease_owner=null,lease_until=null,row_version=row_version+1
where completed and not coalesce("checkUpdates",false) and not coalesce("shouldContinueCheck",true)
  and coalesce(fetch_config->>'mode','browser')<>'health'
  and exists (select 1 from jsonb_array_elements_text(coalesce(m.keywords->'keywords','[]'::jsonb)) k
              where not m.keywords_matched ? lower(k));

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
    keywords_matched=coalesce(p_patch->'keywords_matched',keywords_matched),
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


commit;

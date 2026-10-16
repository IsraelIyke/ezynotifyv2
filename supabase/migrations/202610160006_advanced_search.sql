begin;
create or replace function public.enforce_monitor_failure_limit() returns trigger
language plpgsql set search_path=public as $$
begin
  if old.paused and not new.paused then
    new.failure_count=0;
    new.failure_notified=false;
  elsif coalesce(new.fetch_config->>'mode','browser')<>'health' then
    if new.failure_count>=5 and not new.paused then
      new.paused=true;
      new.lease_owner=null;
      new.lease_until=null;
    elsif new.failure_count=4 and old.failure_count=3 then
      new.next_check_at=greatest(new.next_check_at,now()+interval '12 hours');
    elsif old.failure_count in (3,4) and new.failure_count=old.failure_count then
      new.next_check_at=greatest(new.next_check_at,old.next_check_at);
    end if;
  end if;
  return new;
end $$;

create or replace function public.request_monitor_check(p_id text,p_chat text)
returns text language plpgsql security definer set search_path=public as $$
declare m ezynotify;
begin
  select * into m from ezynotify where id::text=p_id and "telegramID"::text=p_chat for update;
  if not found then return 'missing'; end if;
  if m.paused or m.completed then return 'inactive'; end if;
  if m.failure_count in (3,4) and coalesce(m.fetch_config->>'mode','browser')<>'health'
     and m.next_check_at>now() then return 'advanced_wait'; end if;
  if m.last_manual_at > now()-interval '2 minutes' then return 'cooldown'; end if;
  update ezynotify set next_check_at=now(),last_manual_at=now() where id=m.id;
  return 'queued';
end $$;

-- One-time reset: retain snapshots and completed runs; give stopped runs
-- two advanced attempts instead of repeating the three normal attempts.
create temporary table advanced_reset_ids on commit drop as
  select id from public.ezynotify where paused and not completed and failure_count>=5
  and coalesce(fetch_config->>'mode','browser')<>'health';
update public.ezynotify set paused=false,lease_owner=null,lease_until=null,
  row_version=row_version+1 where id in (select id from advanced_reset_ids);
update public.ezynotify set failure_count=3,failure_notified=true,next_check_at=now()
  where id in (select id from advanced_reset_ids);
update public.notification_outbox set state='cancelled',lease_owner=null,lease_until=null
  where monitor_id in (select id::text from advanced_reset_ids)
  and state='pending' and payload->>'_auto_paused'='true';
commit;

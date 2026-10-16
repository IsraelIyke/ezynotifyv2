begin;
-- Enforce the limit in the database too, including for older in-flight workers.
create or replace function public.enforce_monitor_failure_limit() returns trigger
language plpgsql set search_path=public as $$
begin
  if old.paused and not new.paused then
    new.failure_count=0;
    new.failure_notified=false;
  elsif new.failure_count>=5 and not new.paused then
    new.paused=true;
    new.lease_owner=null;
    new.lease_until=null;
  end if;
  return new;
end $$;
drop trigger if exists enforce_monitor_failure_limit on public.ezynotify;
create trigger enforce_monitor_failure_limit before update on public.ezynotify
  for each row execute function public.enforce_monitor_failure_limit();
revoke all on function public.enforce_monitor_failure_limit() from public,anon,authenticated;

-- Existing failing monitors stop immediately rather than waiting for another run.
with stopped as (
  update public.ezynotify set paused=true,lease_owner=null,lease_until=null
  where not paused and not completed and failure_count>=5
  returning id,"telegramID",title,"checkUpdates"
)
insert into public.notification_outbox(monitor_id,chat_id,channel,payload)
select id::text,"telegramID"::text,'main',jsonb_build_object(
  'text',case when "checkUpdates" then '<b>🔄 PAGE UPDATES</b>' else '<b>🔎 KEYWORD WATCH</b>' end||
    E'\n⏸ <b>Monitor paused</b>\n'||public.escape_alert_html(coalesce(title,'Website monitor'))||
    E'\n\nThis monitor reached five consecutive failed checks. Automatic checks have stopped. Your last successful snapshot is preserved. Open My monitors, review the failure reason, then choose Resume to try again.',
  '_auto_paused',true,'parse_mode','HTML',
  'reply_markup',jsonb_build_object('inline_keyboard',jsonb_build_array(jsonb_build_array(jsonb_build_object('text','📋 My monitors','callback_data','list:0')))))
from stopped where "telegramID" is not null;
commit;

begin;
create table if not exists public.bot_users (
  chat_id text primary key,
  username text,
  first_name text,
  last_name text,
  language_code text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz,
  interaction_count bigint not null default 0,
  last_update_id bigint,
  profile_observed boolean not null default false
);
-- Historical owners count as known users, but not as recently active users.
insert into public.bot_users(chat_id,first_seen_at)
  select "telegramID"::text,min(created_at) from public.ezynotify
  where "telegramID" is not null and "telegramID"::text ~ '^[0-9]+$'
  group by "telegramID" on conflict(chat_id) do nothing;
alter table public.bot_users enable row level security;
revoke all on public.bot_users from anon,authenticated;
grant select,insert,update,delete on public.bot_users to service_role;
create table if not exists public.bot_preferences (
  chat_id text primary key,
  timezone text not null default 'UTC',
  updated_at timestamptz not null default now()
);
alter table public.bot_preferences enable row level security;
revoke all on public.bot_preferences from anon,authenticated;
grant select,insert,update,delete on public.bot_preferences to service_role;

create or replace function public.record_bot_user(p_chat text,p_profile jsonb,p_update_id bigint)
returns void language sql security definer set search_path=public as $$
  insert into bot_users(chat_id,username,first_name,last_name,language_code,last_seen_at,interaction_count,last_update_id,profile_observed)
    values(p_chat,p_profile->>'username',p_profile->>'first_name',p_profile->>'last_name',p_profile->>'language_code',now(),1,p_update_id,true)
  on conflict(chat_id) do update set username=excluded.username,first_name=excluded.first_name,
    last_name=excluded.last_name,language_code=excluded.language_code,last_seen_at=now(),profile_observed=true,
    interaction_count=bot_users.interaction_count+case when bot_users.last_update_id is distinct from p_update_id then 1 else 0 end,
    last_update_id=p_update_id;
$$;
revoke all on function public.record_bot_user(text,jsonb,bigint) from public,anon,authenticated;
grant execute on function public.record_bot_user(text,jsonb,bigint) to service_role;

create or replace view public.bot_usage_stats with (security_invoker=true) as
select count(*)::integer as total_users,
  count(*) filter(where last_seen_at>now()-interval '24 hours')::integer as active_last_24h,
  count(*) filter(where last_seen_at>now()-interval '7 days')::integer as active_last_7_days,
  count(*) filter(where last_seen_at>now()-interval '30 days')::integer as active_last_30_days,
  count(*) filter(where exists(select 1 from public.ezynotify m where m."telegramID"::text=bot_users.chat_id))::integer as users_with_monitors,
  (select count(*)::integer from public.ezynotify) as total_monitors,
  (select count(*)::integer from public.ezynotify where not paused and not completed) as active_monitors
from public.bot_users;
revoke all on public.bot_usage_stats from anon,authenticated;
grant select on public.bot_usage_stats to service_role;
commit;

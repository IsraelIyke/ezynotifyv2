-- Run after the migration and after provisioning the two ezynotify_* Vault
-- secrets using tool/deploy.py schedule. This does not modify other cron jobs.
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
select cron.schedule('ezynotify-dispatch-every-minute','* * * * *',$job$
  select net.http_post(
    url:=(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_project_url')||'/functions/v1/ezynotify-dispatch',
    headers:=jsonb_build_object('Content-Type','application/json','x-dispatch-secret',(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_dispatch_secret')),
    body:='{}'::jsonb,timeout_milliseconds:=20000
  );
$job$);
select cron.schedule('ezynotify-privacy-notifications-every-minute','* * * * *',$job$
  select net.http_post(
    url:=(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_project_url')||'/functions/v1/ezynotify-privacy',
    headers:=jsonb_build_object('Content-Type','application/json','x-dispatch-secret',(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_dispatch_secret')),
    body:='{}'::jsonb,timeout_milliseconds:=55000
  );
$job$);
select cron.schedule('ezynotify-privacy-retention','27 3 * * *',$job$
  delete from public.privacy_admin_updates where created_at<now()-interval '7 days';
  delete from public.privacy_admin_sessions where expires_at<now();
  delete from public.privacy_outbox where state<>'pending' and created_at<now()-interval '30 days';
  delete from public.privacy_requests where completed_at<now()-interval '90 days';
$job$);
select cron.schedule('ezynotify-retention','17 3 * * *',$job$
  delete from public.bot_updates where created_at<now()-interval '7 days';
  delete from public.bot_sessions where expires_at<now();
  delete from public.fetch_cache where fetched_at<now()-interval '7 days';
  delete from public.monitor_events where created_at<now()-interval '90 days';
  delete from public.notification_outbox where state<>'pending' and created_at<now()-interval '30 days';
$job$);
select cron.schedule('ezynotify-admin-notifications-every-minute','* * * * *',$job$
  select net.http_post(
    url:=(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_project_url')||'/functions/v1/ezynotify-notifications',
    headers:=jsonb_build_object('Content-Type','application/json','x-dispatch-secret',(select decrypted_secret from vault.decrypted_secrets where name='ezynotify_dispatch_secret')),
    body:='{}'::jsonb,timeout_milliseconds:=55000
  );
$job$);

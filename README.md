# EzyNotify 🔔

**Your websites. Your updates. Less checking.**

Monitor public pages for changes, keywords or website availability and receive clear Telegram alerts. Page-change alerts include AI summaries. This repository unifies the former `ezynotify-bot` webhook and `ezynotifyv2` checker.

[Open @ezynotify_bot](https://t.me/ezynotify_bot)

## Architecture

```text
Telegram messages & buttons
           │
           ▼
Supabase: ezynotify-telegram ──► saved monitors & durable drafts
           │                              │
           └── “Check now” ──┐             │
                            ▼             │
Supabase Cron ──► ezynotify-dispatch        │
                            │             │
                     GitHub Actions       │
                            ▼             ▼
                     Python / Playwright worker
                            │
                            ├──► ezynotify-ai ──► AI service
                            └──► atomic snapshots, history & notification outbox
                                              │
                                              ▼
                                         Telegram alerts
```

The conversation handler, AI gateway, and scheduling endpoint run in **Supabase Edge Functions**. Chromium runs on a **Windows GitHub Actions runner**, where a full browser is available. Vercel is no longer required. Supabase Cron checks for due work every minute; individual monitors choose intervals from 10 minutes to one day. GitHub queue/startup time can delay actual checks.

## What the bot supports

- A dashboard with blue primary buttons, green save buttons, red delete confirmations, and concise emoji-based navigation.
- Page-change, keyword and Website Health monitors with names and schedules. Page-change and keyword modes also support repeat/one-shot behavior and alert detail preferences.
- Durable, expiring drafts; nothing is created or edited until the user confirms. Cancelling an edit leaves the saved monitor intact.
- Paginated monitor lists, pause/resume, manual checks with a two-minute cooldown, and recent activity.
- Saved timezone preferences for local monitor times, activity and alert timestamps, including daylight saving. Durations display as minutes, hours or days.
- Browser rendering with bounded readiness waits, optional scrolling, CSS region targeting, ignored regions, iframe targeting, and open shadow-root extraction.
- HTML, RSS/Atom, text and JSON modes for sources that do not need a browser; conditional HTTP requests reuse valid ETags/Last-Modified values.
- Shared fetches for identical URLs/configurations, short-lived caching, per-domain pacing, bounded concurrency and retry/backoff. The service does not fetch or enforce robots.txt; users must check website rules and permissions themselves.
- Case-insensitive keyword appearance alerts. Repeating monitors re-arm a keyword after it disappears. One-shot monitors finish after their first match/change.
- AI as the primary change summarizer, with labeled factual excerpts if AI is unavailable.
- All user notifications arrive in the main bot, with distinct **🔎 KEYWORD WATCH** and **🔄 PAGE UPDATES** labels, including failure and recovery alerts. Operator failure diagnostics use a separate admin bot.
- CAPTCHA/challenge and login/access-error detection. Blocked reads preserve the last good snapshot, send one notice per failure episode, back off, and announce recovery.
- Atomic baseline/history/outbox writes, stale-result rejection after user edits, worker leases, workflow concurrency protection, and retried notifications.

The profile image is untouched. Telegram controls the chat theme; button colors depend on client support.

## Repository layout

```text
supabase/functions/ezynotify-telegram/  Telegram webhook
supabase/functions/ezynotify-dispatch/  Authenticated scheduler endpoint
supabase/functions/ezynotify-ai/        Authenticated AI gateway
supabase/functions/_shared/            Bot flows and service clients
supabase/schema.sql                    Initial table for fresh projects
supabase/migrations/                   Upgrade for the legacy table
supabase/schedule.sql                  Cron dispatch and retention jobs
monitor/                              Browser fetcher, processor and worker
ezynotify.py                          Worker entrypoint
.github/workflows/ezynotify.yml        Manually dispatched browser worker
test/                                 Bot, PostgreSQL, worker and Chromium tests
tool/                                 Deployment and verification helpers
.env.example                          Settings template (no real secrets)
```

## Deploy

Requirements: a Supabase project, a Telegram bot token from BotFather, an AI service key, a GitHub repository with Actions, Python 3.12+, Node.js 22+, and the Supabase CLI. Python deployment helpers also use PyNaCl when setting encrypted GitHub secrets.

### 1. Database

On a fresh project, run `supabase/schema.sql` first. On both fresh and existing projects, apply the files in `supabase/migrations/` in order. Existing rows are retained. The migrations add scheduling/status fields, sessions, history, leases, fetch cache, notification outbox, user records and timezone preferences.

Internal tables and RPCs are restricted to `service_role`. The webhook checks Telegram chat ownership, and the manual-check transaction checks ownership again. Never expose the service-role key in client-side code.

### 2. Supabase function secrets

| Secret | Purpose |
| --- | --- |
| `BOT_TOKEN` | Main Telegram bot for conversations and alerts |
| `ADMIN_BOT_TOKEN` | Separate Telegram bot for operator failure diagnostics; Supabase only |
| `TELEGRAM_WEBHOOK_SECRET` | Validates Telegram's secret header |
| `DISPATCH_SECRET` | Authenticates Cron/admin requests to the dispatcher |
| `MONITOR_AI_SECRET` | Authenticates worker requests to the AI gateway |
| AI credentials and model settings | Set the provider key, summary model, optional vision model and reasoning options using the exact secret names in [the configuration template](.env.example) |
| `GITHUB_REPOSITORY` | `owner/repository` |
| `GITHUB_WORKFLOW` | `ezynotify.yml` |
| `GITHUB_REF` | Worker branch, normally `main` |
| `GITHUB_DISPATCH_TOKEN` | Fine-grained repository token with Actions: write |

Supabase automatically supplies `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; do not upload those reserved names as custom function secrets.

**Change the summarizer anytime:** update the summary-model secret listed in [.env.example](.env.example) under Supabase → Edge Functions → Secrets. No code change or GitHub secret change is needed; requests use the new setting after secret propagation. Update or clear the optional reasoning setting if the replacement model supports different parameters. Leave the vision-model secret empty to disable AI classification. Model availability depends on your provider account.

Deploy:

```bash
supabase functions deploy ezynotify-telegram --project-ref YOUR_REF --use-api
supabase functions deploy ezynotify-dispatch --project-ref YOUR_REF --use-api
supabase functions deploy ezynotify-ai --project-ref YOUR_REF --use-api
```

Their platform JWT checks are disabled in `supabase/config.toml` because each function validates its own secret header. Do not remove those checks.

### 3. GitHub worker secrets

Configure `SUPABASE_URL`, `SUPABASE_KEY` (service-role key), `BOT_TOKEN`, `MONITOR_AI_ENDPOINT` and `MONITOR_AI_SECRET` in GitHub Actions. The AI endpoint is `https://YOUR_REF.supabase.co/functions/v1/ezynotify-ai`.

The worker needs neither the AI service key nor a model ID in production. Both stay in Supabase. All keyword, change, failure and recovery notifications use the same `BOT_TOKEN` as the conversation handler. Separate keyword/update bots are no longer used; a missing main token fails delivery and preserves the queued alert for retry.

Push the worker workflow to GitHub. It declares `workflow_dispatch`, an optional `monitor_id` input, a 20-minute job timeout and concurrency protection. It installs the matching Playwright Chromium version. It has no independent cron schedule, so Supabase is the scheduling authority.

### 4. Cron and Telegram cutover

Store `ezynotify_project_url` and `ezynotify_dispatch_secret` in Supabase Vault, then run `supabase/schedule.sql`. The dispatcher skips idle batches, coalesces requests, and avoids dispatching while a worker lease is active. The retention job prunes expired sessions, old events and delivered notifications.

Register the Telegram webhook at `https://YOUR_REF.supabase.co/functions/v1/ezynotify-telegram`, with `secret_token` equal to `TELEGRAM_WEBHOOK_SECRET`, `allowed_updates` set to `message` and `callback_query`, and `max_connections` set to 1. Keep pending updates when migrating. Disable the old checker schedule before switching so two implementations cannot update the same baselines.

The helper `tool/deploy.py bot` validates `@ezynotify_bot`, updates its long/short description, commands and command menu, and switches its webhook. It never calls a profile-photo method. After cutover, disable the old Vercel deployment in its dashboard: switching a Telegram webhook does not disable the former public endpoint.

Deployment helpers read private credentials from ignored `.secrets/deployment.env`. `tool/prepare-deployment.py` is a convenience for this workspace's user-authorized credential locations; other installations should provide their own environment/secrets. Never commit `.secrets`, `.env`, or secret-bearing logs.

## Local use and tests

```bash
npm ci
python -m venv .venv
# Activate .venv for your shell, then:
pip install -r requirements.txt
python -m playwright install chromium
npm run check
npm test
python -m unittest discover -s test -p 'test_*.py' -v
```

Tests use mocked service clients, embedded PostgreSQL and real Chromium with in-memory page fixtures. They do not send Telegram messages or write to your live Supabase project.

To run a real worker pass, copy `.env.example` to `.env`, configure the worker settings, then run `python ezynotify.py`. This reads/writes Supabase and can send real alerts. For local direct AI calls without the Edge gateway, omit `MONITOR_AI_ENDPOINT` and supply the provider credentials and model settings from the configuration template.

## Commands

`/start` opens the dashboard, `/new` creates a monitor, `/monitors` opens the list, `/timezone` opens local-time settings, `/help` explains monitoring, and `/cancel` discards a draft. Existing `/new_update_monitor`, `/new_keyword_check`, `/list_update_requests` and `/list_keyword_check_requests` commands remain available. Editing/deletion use inline buttons.

Choose a timezone with the dashboard button or send `/timezone Africa/Lagos` (or another IANA city timezone). Telegram does not provide the device timezone, so UTC is used until the user chooses. Stored timestamps stay in UTC; display times use the saved preference. Check intervals remain elapsed durations.

## User records and usage counts

The admin-only `bot_users` table records Telegram ID, username, first/last name, language, first/last interaction time and interaction count. Profiles update when the user interacts; duplicate update retries do not inflate the counter. Historical monitor owners are backfilled with unknown names and no invented recent activity.

In the Supabase SQL editor, run:

```sql
select * from public.bot_usage_stats;
```

The view reports total users, active users over 24 hours/7 days/30 days, users with monitors, and total/active monitors. Activity means bot interaction, not background monitoring. These records and preferences are protected by row-level security and are unavailable to public API keys.

## Operational limits

There is no undetectable mode and no CAPTCHA bypass. Use public APIs/feeds or arrange allowlisting when available. AI can help classify a block but does not solve human-verification challenges.

Only public HTTP(S) destinations on ports 80/443 are supported. Private/reserved destinations, credentialed URLs and unsafe redirects are blocked. All browser HTTP traffic passes through an IP-validating transport; service workers and WebSockets are disabled. Browser POST requests are blocked, so sites needing POST-based data APIs may require an official feed/API integration.

Rendering is bounded: 120 requests, 3 MB per resource, 120,000 text characters, five scroll steps, one configured iframe, and open shadow roots. Closed shadow roots, login sessions, arbitrary click flows and unlimited scrolling are not supported. Specify a target selector for slowly loading pages; text stability is a heuristic, not proof that every background request finished.

Up to 50 monitors per chat are allowed. A worker claims 20 due monitors per pass. For substantial volume or consistently low-latency checks, replace the Actions worker with a persistent browser service while keeping the same database contracts.

Delivery is at least once: a Telegram send that succeeds just before a worker crash can be repeated because Telegram has no send-message idempotency key. Stored result/outbox transactions prevent ordinary reprocessing duplicates, but cannot eliminate that external-service crash window.

## License

MIT. Both original components are by Israel Ikechukwu. See [LICENSE](LICENSE).
## Admin alerts and privacy

### Website Health

Choose **New monitor → Website health**, or send `/health`. Add a URL, name and check interval (10 minutes or longer). The main bot sends **🌐 WEBSITE HEALTH** alerts once when the monitored URL becomes unavailable and once when it recovers. A first check that is already down also alerts; a first successful check establishes availability silently.

Health checks use a bounded GET request and inspect HTTP response headers, following validated public redirects without extracting content or calling AI. HTTP 2xx/3xx responses indicate availability; network/DNS/TLS failures and other HTTP errors indicate that the monitored URL is unavailable from the checker. HTTP 401, 403 and 429 are inconclusive access restrictions, with a separate notice rather than a false outage or recovery. A 200 response only establishes HTTP availability, not that all application features work or that every visitor can reach the site.

Health monitors continue through outages to detect recovery and are exempt from the five-failure automatic pause. They respect rate limits and configured intervals. Detection can be delayed by scheduling and GitHub runner startup. Use Pause to stop checks manually. Updates and Keywords retain their five-failure limit.

Updates and Keywords switch to advanced search after three consecutive failed checks. The first advanced attempt is due immediately; the second is due 12 hours after the first fails (or later if the website requests a longer rate-limit pause). Both failing brings the total to five and pauses the monitor. Advanced search uses pywinauto to navigate a visible, isolated Chromium window and Playwright to scroll and extract content. The worker requires an unlocked interactive Windows desktop; desktop input failures do not consume website attempts. Manual checks cannot shorten the advanced-search wait. Migration `202610160006_advanced_search.sql` reactivates non-completed monitors stopped at five failures with two advanced attempts remaining, preserving their snapshots. Health monitors retain their existing behavior. A successful check resets the failure streak. Users receive a pause notice and can review the reason under My monitors, then choose Resume to start a fresh attempt cycle. Existing monitors already at the limit are paused by the migration. Internal HTTP retries do not count as separate checks.

Every completed failed website check queues an admin alert in Supabase, including consecutive failures. Configure the recipient in the service-only `monitor_admin_settings` table and the separate bot token as the Supabase secret `ADMIN_BOT_TOKEN`. The `ezynotify-notifications` Edge Function uses this separate bot; an authenticated Supabase Cron job runs every minute. It requires that secret and does not fall back to the main bot. User keyword, change, failure and recovery notices continue through the main bot. Delivery retries are independent of user-facing failure suppression. Alerts include the hostname, monitor ID/type, reason and timestamp, excluding URL paths, queries and page content.

The `/privacy` command and dashboard button explain responsible monitoring, robots.txt, permissions, data processing and limitations of liability. See [PRIVACY.md](PRIVACY.md). Access, correction and deletion requests are recorded in the service-only `privacy_requests` table for manual operator review. Review this table regularly; submitting a request does not automatically delete records.

### Optional success screenshots

Enable **Screenshots** in the monitor review or Advanced settings. This selects browser rendering; switching to HTML/feed disables screenshots. Normal Playwright checks and advanced desktop checks both support it. New keyword matches and content updates receive a separate cropped JPEG of the first matching region, highlighted when located. Deletions or unlocatable text fall back to the current monitored area with an explanatory caption. Baselines, unchanged checks and failures do not send images. Screenshot failures preserve the text alert. Images come from the same browser page as the text and are stored in the notification outbox for delivery/retry, excluded from the shared fetch cache. Existing outbox retention applies.

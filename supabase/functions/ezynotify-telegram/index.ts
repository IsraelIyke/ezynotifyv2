import { createBot } from '../_shared/bot.mjs';
import { createDatabase, createTelegram, dispatchChecks, envRequired, secureEquals } from '../_shared/services.mjs';

const env = (name: string) => Deno.env.get(name);
Deno.serve(async (req: Request) => {
  if (req.method !== 'POST') return new Response('Method not allowed', { status: 405 });
  if (!await secureEquals(req.headers.get('x-telegram-bot-api-secret-token'), env('TELEGRAM_WEBHOOK_SECRET'))) return new Response('Unauthorized', { status: 401 });
  let update;
  try {
    const raw=await req.text();
    if(raw.length>65536)return new Response('Payload too large',{status:413});
    update=JSON.parse(raw);
  } catch { return new Response('Invalid payload', {status:400}); }
  if (!Number.isSafeInteger(update.update_id)) return new Response('Invalid update', {status:400});
  const db=createDatabase(envRequired(env,'SUPABASE_URL'),envRequired(env,'SUPABASE_SERVICE_ROLE_KEY'));
  const telegram=createTelegram(envRequired(env,'BOT_TOKEN'));
  const chat=update.message?.chat || update.callback_query?.message?.chat;
  const owner=crypto.randomUUID();
  let locked=false;
  try {
    if(chat) {
      locked=await db.rpc('acquire_runtime_lock',{p_name:`chat:${chat.id}`,p_owner:owner,p_seconds:60});
      if(!locked)return new Response('Busy; retry',{status:503});
    }
    if(!await db.rpc('claim_bot_update',{p_id:update.update_id}))return new Response('OK');
    const bot=createBot({db,telegram,dispatch:()=>dispatchChecks(db,env)});
    await bot(update);
    await db.request(`bot_updates?update_id=eq.${update.update_id}`,{method:'PATCH',body:{state:'done'}});
    return new Response('OK');
  } catch {
    // Allow a retried update to acquire its claim again. All edits require an
    // explicit confirmation; sessions are durable across isolate restarts.
    await db.request(`bot_updates?update_id=eq.${update.update_id}&state=eq.processing`,{method:'DELETE'}).catch(()=>{});
    console.error('Telegram update failed', update.update_id);
    return new Response('Retry later',{status:503});
  } finally {
    if(locked)await db.request(`runtime_locks?name=eq.${encodeURIComponent(`chat:${chat.id}`)}&owner=eq.${owner}`,{method:'DELETE'}).catch(()=>{});
  }
});

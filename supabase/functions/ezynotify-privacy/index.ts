import {createPrivacyAdmin,deliverPrivacyNotifications} from '../_shared/privacy-admin.mjs';
import {createDatabase,createTelegram,envRequired,secureEquals} from '../_shared/services.mjs';
const env=(name:string)=>Deno.env.get(name);
Deno.serve(async(req:Request)=>{
  if(req.method!=='POST')return new Response('Method not allowed',{status:405});
  const dispatch=await secureEquals(req.headers.get('x-dispatch-secret'),env('DISPATCH_SECRET'));
  if(!dispatch&&!await secureEquals(req.headers.get('x-telegram-bot-api-secret-token'),env('PRIVACY_WEBHOOK_SECRET')))return new Response('Unauthorized',{status:401});
  const db=createDatabase(envRequired(env,'SUPABASE_URL'),envRequired(env,'SUPABASE_SERVICE_ROLE_KEY'));
  const adminTelegram=createTelegram(envRequired(env,'PRIVACY_BOT_TOKEN'));
  const userTelegram=createTelegram(envRequired(env,'BOT_TOKEN'));
  if(dispatch){
    try{
      const sendFile=async(chat:string,content:string,caption:string,filename:string)=>{
        const data=new FormData();data.set('chat_id',chat);data.set('caption',caption);
        data.set('document',new Blob([content],{type:'application/json'}),filename);
        const response=await fetch(`https://api.telegram.org/bot${envRequired(env,'BOT_TOKEN')}/sendDocument`,{method:'POST',body:data,signal:AbortSignal.timeout(20000)});
        const body=await response.json();if(!response.ok||!body.ok)throw Error('File delivery failed');
      };
      return Response.json(await deliverPrivacyNotifications({db,adminTelegram,userTelegram,sendFile}));
    }catch{console.error('Privacy notification delivery unavailable.');return new Response('Retry later',{status:503});}
  }
  let update;
  try{const raw=await req.text();if(raw.length>65536)return new Response('Payload too large',{status:413});update=JSON.parse(raw);}catch{return new Response('Invalid update',{status:400});}
  if(!Number.isSafeInteger(update.update_id))return new Response('Invalid update',{status:400});
  const actor=update.callback_query?.from||update.message?.from,chat=update.callback_query?.message?.chat||update.message?.chat;
  const settings=(await db.request('privacy_admin_settings?singleton=eq.true&limit=1'))?.[0];
  if(!settings?.enabled||actor?.is_bot||chat?.type!=='private'||String(actor?.id)!==String(settings.admin_user_id)||String(chat.id)!==String(settings.chat_id))return new Response('OK');
  const owner=crypto.randomUUID(),name=`privacy-admin:${actor.id}`;let locked=false;
  try{
    locked=await db.rpc('acquire_runtime_lock',{p_name:name,p_owner:owner,p_seconds:90});
    if(!locked)return new Response('Busy; retry',{status:503});
    if(!await db.rpc('claim_privacy_update',{p_id:update.update_id}))return new Response('OK');
    await createPrivacyAdmin({db,telegram:adminTelegram})(update);
    await db.request(`privacy_admin_updates?update_id=eq.${update.update_id}`,{method:'PATCH',body:{state:'done'}});
    return new Response('OK');
  }catch{
    await db.request(`privacy_admin_updates?update_id=eq.${update.update_id}&state=eq.processing`,{method:'DELETE'}).catch(()=>{});
    console.error('Privacy admin action could not complete.');return new Response('Retry later',{status:503});
  }finally{if(locked)await db.request(`runtime_locks?name=eq.${encodeURIComponent(name)}&owner=eq.${owner}`,{method:'DELETE'}).catch(()=>{});}
});

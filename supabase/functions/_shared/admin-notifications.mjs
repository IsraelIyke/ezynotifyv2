import { formatTimestamp } from './time.mjs';
const q=encodeURIComponent;
export async function deliverAdminNotifications({db,telegram,now=()=>Date.now(),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}) {
  const owner=crypto.randomUUID(),deadline=now()+45000;
  const rows=await db.rpc('claim_admin_notifications',{p_owner:owner,p_limit:20});
  const zones=new Map();let sent=0,failed=0;
  try {
    for(const item of rows||[]) {
      if(now()>deadline)break;
      const filter=`notification_outbox?id=eq.${item.id}&lease_owner=eq.${owner}`;
      try {
        if(!zones.has(item.chat_id)) {
          const preferences=await db.request(`bot_preferences?chat_id=eq.${q(item.chat_id)}&select=timezone&limit=1`);
          zones.set(item.chat_id,preferences?.[0]?.timezone||'UTC');
        }
        const payload={...item.payload,chat_id:item.chat_id};
        const date=payload._checked_at||item.created_at;delete payload._checked_at;
        payload.text=(payload.text||'').replace(/\u2014/g,'-')+`\n\n🕒 ${formatTimestamp(date,zones.get(item.chat_id))}`;
        await telegram('sendMessage',payload);
        await db.request(filter,{method:'PATCH',body:{state:'sent',sent_at:new Date(now()).toISOString(),lease_owner:null,lease_until:null,last_error:null}});
        sent++;
      } catch {
        failed++;
        const attempts=(item.attempts||0)+1;
        await db.request(filter,{method:'PATCH',body:{state:attempts>=20?'failed':'pending',attempts,
          next_attempt_at:new Date(now()+Math.min(3600000,60000*2**Math.min(attempts,6))).toISOString(),
          lease_owner:null,lease_until:null,last_error:'Admin notification delivery failed; check the destination and bot access.'}});
      }
      await sleep(1100);
    }
  } finally {
    await db.request(`notification_outbox?lease_owner=eq.${owner}&state=eq.pending`,{method:'PATCH',body:{lease_owner:null,lease_until:null}});
  }
  return {sent,failed};
}

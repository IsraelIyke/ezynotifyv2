const q=encodeURIComponent;
const html=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const button=(text,data)=>({text,callback_data:data});
const back=[[button('← Requests','pv:list:0')]];
const errors={missing:'Request not found.',closed:'This request is already closed.',not_deletion:'This is not a deletion request.',wrong_kind:'This action does not apply to this request type.',already_erased:'Active erasure has already been recorded. Review external handling before completing the request.',expired_confirmation:'The deletion confirmation expired. Open a new preview.',worker_busy:'A monitoring worker is active. No data was erased. Retry after it finishes.',chat_busy:'The user is currently interacting with the bot. No data was erased. Retry shortly.',external_review_required:'Erase active data and record backup/provider handling before completing deletion.',note_required:'Please provide a meaningful explanation.',note_too_long:'Keep the message below 2,000 characters.',invalid_fields:'Only first_name, last_name, username, language_code and timezone can be corrected; use string values up to 100 characters.',invalid_timezone:'The timezone is not recognized.',profile_missing:'There is no stored profile to correct.',new_activity:'The user has used the service again after erasure. Review this new activity before claiming completion.',access_pending:'An access request is also open for this user. Handle that request before erasing their records.'};

errors.privacy_delivery_busy='A privacy response is currently being delivered. No data was changed. Retry shortly.';
errors.outcome_too_long='The outcome and backup/provider note together exceed the message limit. Shorten one of them and confirm again.';

export function createPrivacyAdmin({db,telegram,now=()=>new Date()}){
  const request=async id=>(await db.request(`privacy_requests?id=eq.${q(id)}&limit=1`))?.[0];
  const session=async actor=>(await db.request(`privacy_admin_sessions?admin_id=eq.${q(actor)}&expires_at=gt.${q(now().toISOString())}&limit=1`))?.[0];
  const clear=actor=>db.request(`privacy_admin_sessions?admin_id=eq.${q(actor)}`,{method:'DELETE'});
  const save=(actor,id,action,draft={})=>db.request('privacy_admin_sessions?on_conflict=admin_id',{method:'POST',prefer:'resolution=merge-duplicates',body:{admin_id:actor,request_id:id,action,draft,expires_at:new Date(now().getTime()+1800000).toISOString()}});
  async function screen(chat,text,rows=back,mid){
    const body={chat_id:chat,text,parse_mode:'HTML',reply_markup:{inline_keyboard:rows}};
    if(mid){try{return await telegram('editMessageText',{...body,message_id:mid});}catch{}}
    return telegram('sendMessage',body);
  }
  async function list(chat,page=0,mid,closed=false){
    page=Math.max(0,Math.min(10000,Number(page)||0));
    const rows=await db.request(`privacy_requests?status=${closed?'eq.completed':'in.(pending,in_review)'}&order=${closed?'completed_at.desc':'created_at.asc'}&limit=11&offset=${page*10}`);
    const buttons=(rows||[]).slice(0,10).map(r=>[button(`#${r.id} · ${r.kind} · ${r.erase_queued_at?'deletion queued':r.active_erased_at?'active data erased':r.status}`,`pv:view:${r.id}`)]);
    if(page>0)buttons.push([button('← Previous',`pv:${closed?'closed':'list'}:${page-1}`)]);
    if(rows?.length>10)buttons.push([button('Next →',`pv:${closed?'closed':'list'}:${page+1}`)]);
    buttons.push([button(closed?'Open requests':'Recent closed requests',closed?'pv:list:0':'pv:closed:0')]);
    buttons.push([button('Refresh','pv:list:0')]);
    return screen(chat,'<b>Privacy requests</b>\n\n'+(rows?.length?'Select a request. Review is manual and optional; opening a request does not erase data.':'There are no open requests.'),buttons,mid);
  }
  async function view(chat,id,mid,notice=''){
    const r=await request(id);
    if(!r)return screen(chat,'Request not found.',back,mid);
    const deliveries=await db.request(`privacy_outbox?request_id=eq.${q(id)}&select=state,destination&limit=1000`);
    const pending=(deliveries||[]).filter(d=>d.state==='pending').length,failed=(deliveries||[]).filter(d=>d.state==='failed').length;
    const buttons=[];
    if(r.status!=='completed'){
      buttons.push([button('Reply / ask for details',`pv:reply:${id}`)]);
      if(r.kind==='access')buttons.push([button('Send account/settings export',`pv:export:${id}`)]);
      if(r.kind==='correction')buttons.push([button('Apply app-profile correction',`pv:correct:${id}`)]);
      if(r.kind==='deletion'){
        if(r.erase_queued_at)buttons.push([button('Cancel queued deletion',`pv:cancel_erasure:${id}`)]);
        else buttons.push([button(r.active_erased_at?'Check for new account data…':'Delete user data…',`pv:preview:${id}`)]);
        if(r.active_erased_at)buttons.push([button('Backup/provider checklist',`pv:handling:${id}`)]);
      }
      if(r.kind!=='deletion'||(r.active_erased_at&&r.external_review_note))buttons.push([button('Finish request',`pv:outcome:${id}`)]);
      buttons.push([button('More options',`pv:options:${id}`)]);
      if(!r.active_erased_at)buttons.push([button('Decline with explanation',`pv:decline:${id}`)]);
    }
    if(failed)buttons.push([button('Retry failed responses',`pv:retry_delivery:${id}`)]);
    buttons.push(...back);
    const info=`<b>Privacy request #${html(id)}</b>\nType: ${html(r.kind)}\nAccount: <code>${html(r.chat_id)}</code>\nReceived: ${html(r.created_at)}\nState: ${html(r.status)}\nActive data erased: ${r.active_erased_at?'Yes':'No'}\nExternal handling recorded: ${r.external_review_note?'Yes':'No'}`;
    const next=r.status==='completed'?'Finished. The final response is queued or delivered.':r.erase_queued_at?'Deletion queued. Automatic retries run every minute for up to 24 hours while monitoring or delivery is busy. You will be notified when it runs or stops.':r.kind==='deletion'?(!r.active_erased_at?'Next: delete active account data. Any open access request must be finished first.':!r.external_review_note?'Next: confirm how backups and provider copies were handled.':'Next: finish using the prepared reply.'):'Next: carry out the request, then finish using the prepared reply.';
    return screen(chat,(notice?html(notice)+'\n\n':'')+info+`\nPending deliveries: ${pending} · Failed deliveries: ${failed}`+'\n\n'+html(next),buttons,mid);
  }
  return async function handle(update){
    const callback=update.callback_query,message=callback?.message||update.message;
    const actor=String(callback?.from?.id||message?.from?.id||'');
    const settings=(await db.request('privacy_admin_settings?singleton=eq.true&limit=1'))?.[0];
    if(!settings?.enabled || message?.chat?.type!=='private' || actor!==String(settings.admin_user_id) || String(message.chat.id)!==String(settings.chat_id))return;
    const chat=String(message.chat.id),mid=callback?message.message_id:undefined;
    if(callback)try{await telegram('answerCallbackQuery',{callback_query_id:callback.id});}catch{/* Expired callback acknowledgement must not block the action. */}
    const action=async(id,name,note='',token='')=>db.rpc('privacy_action',{p_id:id,p_actor:actor,p_action:name,p_note:note,p_token:token,p_event:String(update.update_id)});
    if(!callback){
      const text=message.text?.trim()||'';
      if(['/start','/requests','/cancel'].includes(text)){await clear(actor);return list(chat);}
      const s=await session(actor);
      if(!s)return list(chat);
      if(text.length>2000||text.length<3)return screen(chat,'Please send 3–2,000 characters, or /cancel.');
      if(['reply','retention'].includes(s.action)){
        const result=await action(s.request_id,s.action,text);
        if(result.error)return screen(chat,errors[result.error]||'This action could not be completed.');
        await clear(actor);return view(chat,s.request_id,undefined,s.action==='reply'?'Response queued for the user.':'External handling recorded.');
      }
      if(['complete','decline','correct'].includes(s.action)){
        if(s.action==='correct'){
          try{const value=JSON.parse(text);if(!value||Array.isArray(value)||typeof value!=='object')throw Error();}catch{return screen(chat,'Send a JSON object, for example <code>{"timezone":"Africa/Lagos"}</code>, or /cancel.');}
        }
        const token=crypto.randomUUID();await save(actor,s.request_id,'confirm',{action:s.action,note:text,token});
        return screen(chat,`<b>Confirm ${html(s.action)} for request #${html(s.request_id)}</b>\n\n${html(text)}\n\n${s.action==='correct'?'This changes stored app details.':'This outcome will be sent to the requester.'}`,
          [[button('Confirm',`pv:confirm:${s.request_id}:${token}`)],[button('← Request',`pv:view:${s.request_id}`)]]);
      }
      return screen(chat,'Use the confirmation button above, or /cancel.');
    }
    const [prefix,verb,id,token='']=String(callback.data||'').split(':');
    if(prefix!=='pv')return;
    if(['list','closed'].includes(verb)){await clear(actor);return list(chat,id,mid,verb==='closed');}
    if(!/^\d+$/.test(id||''))return;
    if(verb==='view'){await clear(actor);return view(chat,id,mid);}
    if(verb==='cancel_erasure'){
      const result=await action(id,'cancel_erasure');
      return view(chat,id,mid,result.error?errors[result.error]:'Queued deletion cancelled. This cancellation did not erase data.');
    }
    if(verb==='options')return screen(chat,'Optional actions. Use a custom outcome if the prepared reply does not describe what you did.',[
      [button('Write a custom outcome',`pv:complete:${id}`)],
      [button('Start manual review',`pv:review:${id}`)],
      [button('← Request',`pv:view:${id}`)]],mid);
    if(verb==='handling')return screen(chat,'<b>Backup/provider checklist</b>\n\nActive account data has been erased. Choose the statement you have verified. Telegram may retain messages already delivered; users can remove their own copies. If backups, exports or other provider copies remain, describe them and their retention dates.',[
      [button('Verified: no other retained copies',`pv:nocopies:${id}`)],
      [button('Copies remain / add details',`pv:retention:${id}`)],
      [button('← Request',`pv:view:${id}`)]],mid);
    if(['outcome','nocopies'].includes(verb)){
      const r=await request(id);if(!r||r.status==='completed')return view(chat,id,mid,'This request is closed.');
      let note,actionName='complete';
      if(verb==='nocopies'){
        if(r.kind!=='deletion'||!r.active_erased_at)return view(chat,id,mid,'Erase active account data first.');
        actionName='retention';note='The operator verified that no additional service-controlled backups, local exports or provider copies requiring action are retained. Telegram may retain previously delivered messages, which users can remove from their own chats.';
      }else{
        if(r.kind==='deletion'&&(!r.active_erased_at||!r.external_review_note))return view(chat,id,mid,errors.external_review_required);
        const performed=await db.request(`privacy_audit?request_id=eq.${q(id)}&action=eq.${r.kind==='access'?'export':'correct'}&limit=1`);
        if(['access','correction'].includes(r.kind)&&!performed?.length)return view(chat,id,mid,r.kind==='access'?'Send the account/settings export first, then finish the request.':'Apply the requested app-profile correction first, or use a custom outcome in More options.');
        note={access:'Your account/settings export has been prepared and queued for delivery. It contains your stored profile, timezone and monitor settings. The operator has reviewed whether additional records need to be provided separately. Contact us if you need clarification or further records.',deletion:'Your active EzyNotify account data has been erased. Backup/provider handling is described below. Previously delivered Telegram messages may remain in your chat.',correction:'The requested correction to your stored EzyNotify app details has been applied. This does not change your Telegram account profile.',other:'Your privacy request has been reviewed and the requested action has been carried out. Contact us if you need clarification or further assistance.'}[r.kind];
      }
      const confirmation=crypto.randomUUID();await save(actor,id,'confirm',{action:actionName,note,token:confirmation});
      return screen(chat,`<b>${verb==='nocopies'?'Confirm backup/provider statement':'Finish request #'+html(id)}</b>\n\n${html(note)}\n\n${verb==='nocopies'?'Confirm only if you have checked this statement.':'Confirm only if this describes the actual outcome.'}`,[
        [button(verb==='nocopies'?'Confirm statement':'Confirm & finish',`pv:confirm:${id}:${confirmation}`)],
        [button('Edit wording',`pv:${verb==='nocopies'?'retention':'complete'}:${id}`)],
        [button('← Request',`pv:view:${id}`)]],mid);
    }
    if(verb==='preview'){
      await clear(actor);const result=await action(id,'preview');
      if(result.error)return view(chat,id,mid,errors[result.error]);
      const counts=Object.entries(result.counts).map(([key,value])=>`${html(key.replaceAll('_',' '))}: ${html(value)}`).join('\n');
      return screen(chat,`<b>Delete active user data?</b>\nRequest #${html(id)}\nAccount: <code>${html(result.account)}</code>\n\n${counts}\n\nThis permanently removes the account's monitors, snapshots, screenshots in delivery records, history, profile, preferences and drafts. The shared fetch cache is also cleared and will rebuild.\n\nIf monitoring or delivery is busy, your confirmation queues automatic retries for up to 24 hours. You can cancel while it is queued.\n\nLocal backups and copies held by Telegram/providers require separate handling. The request record remains open until that handling is recorded. Confirm only after reviewing the request and any retention obligations.`,
        [[button('Confirm permanent deletion',`pv:erase:${id}:${result.token}`)],[button('← Request',`pv:view:${id}`)]],mid);
    }
    if(verb==='erase'){
      const result=await action(id,'erase','',token);
      if(result.error==='access_pending'){
        const related=await db.request(`privacy_requests?chat_id=eq.${q((await request(id)).chat_id)}&kind=eq.access&status=in.(pending,in_review)&limit=1`);
        return screen(chat,'Nothing was deleted. Finish the open access request first so the user can receive their information before it is erased.',[
          ...(related?.length?[[button(`Finish access request #${related[0].id}`,`pv:view:${related[0].id}`)]]:[]),
          [button('← Deletion request',`pv:view:${id}`)]],mid);
      }
      if(result.error)return view(chat,id,mid,errors[result.error]||'Deletion did not complete.');
      if(result.queued)return view(chat,id,mid,'Deletion queued. No data was erased yet. The bot will retry automatically when monitoring or delivery finishes; you do not need to confirm again.');
      return view(chat,id,mid,'Active user data erased. Review backup/provider handling before completing this request.');
    }
    if(verb==='review'){
      const result=await action(id,'review');
      return view(chat,id,mid,result.error?errors[result.error]:'Manual review started. An acknowledgement is queued for the user.');
    }
    if(verb==='retry_delivery'){
      const result=await action(id,'retry_delivery');return view(chat,id,mid,result.error?errors[result.error]:`${result.requeued} failed response(s) requeued.`);
    }
    if(verb==='export'){
      const r=await request(id);
      if(r?.kind!=='access'||r.status==='completed')return view(chat,id,mid,'This action applies to an open access request.');
      return screen(chat,`Send an account/settings JSON file to the requester for #${html(id)}?\n\nIt includes their stored profile, timezone and monitor settings. It excludes snapshots, screenshots and historical check/delivery records. Review those separately when relevant; this export alone does not complete the access request.`,
        [[button('Send to requester',`pv:sendexport:${id}`)],[button('← Request',`pv:view:${id}`)]],mid);
    }
    if(verb==='sendexport'){
      const result=await action(id,'export');return view(chat,id,mid,result.error?errors[result.error]:'Export queued. One step left: tap “Finish request”, review the wording and confirm.');
    }
    if(verb==='confirm'){
      const s=await session(actor);
      if(!s||String(s.request_id)!==id||s.action!=='confirm'||s.draft.token!==token)return view(chat,id,mid,'This confirmation expired. Start the action again.');
      const result=await action(id,s.draft.action,s.draft.note);
      if(result.error)return view(chat,id,mid,errors[result.error]||'This action could not be completed.');
      await clear(actor);return view(chat,id,mid,s.draft.action==='retention'?'Backup/provider statement saved. Next: finish using the prepared reply.':s.draft.action==='complete'||s.draft.action==='decline'?'Request finished. The final response is queued for the user.':'Correction applied. Next: finish using the prepared reply.');
    }
    if(['reply','retention','complete','decline','correct'].includes(verb)){
      const r=await request(id);if(!r||r.status==='completed')return view(chat,id,mid,'This request is closed.');
      await save(actor,id,verb);
      const prompts={reply:'Send the response or clarification question for this user.',retention:'Describe what happened to backups, local exports and provider-held copies. Include any retained categories, justification, access restrictions and deletion/expiry dates. Avoid unnecessary personal details. This note will be included in the final response.',complete:'Describe the actual outcome for the user. Do not claim that every copy everywhere was deleted. Deletion can only be completed after active erasure and external-handling review.',decline:'Explain the decision, the applicable reason, any retained data and retention period, and how the user can challenge the outcome. This is a manual legal decision.',correct:'Send a JSON object with first_name, last_name, username, language_code and/or timezone. Example: {"timezone":"Africa/Lagos"}. Only stored app details can be corrected; Telegram account details must be changed in Telegram.'};
      return screen(chat,`<b>Request #${html(id)}</b>\n\n${html(prompts[verb])}\n\nSend your message here, or /cancel.`,[[button('← Request',`pv:view:${id}`)]],mid);
    }
  };
}

export async function deliverPrivacyNotifications({db,adminTelegram,userTelegram,sendFile,now=()=>Date.now(),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}){
  const owner=crypto.randomUUID(),deadline=now()+45000;
  const rows=await db.rpc('claim_privacy_notifications',{p_owner:owner,p_limit:20});
  let sent=0,failed=0;
  try{
    for(const item of rows||[]){
      if(now()>deadline)break;
      const filter=`privacy_outbox?id=eq.${item.id}&lease_owner=eq.${owner}`;
      try{
        const payload={...item.payload,chat_id:item.chat_id};
        const receipt=payload._erase_receipt;delete payload._erase_receipt;
        const document=payload._document_json;delete payload._document_json;
        if(document){await sendFile(item.chat_id,JSON.stringify(document,null,2),payload.text,`privacy-request-${item.request_id}.json`);}
        else await (item.destination==='admin'?adminTelegram:userTelegram)('sendMessage',payload);
        const r=(await db.request(`privacy_requests?id=eq.${item.request_id}&select=status,active_erased_at&limit=1`))?.[0];
        const scrub=item.destination==='user'&&(receipt||(r?.status==='completed'&&r.active_erased_at));
        await db.request(filter,{method:'PATCH',body:{state:'sent',sent_at:new Date(now()).toISOString(),lease_owner:null,lease_until:null,
          ...(scrub?{chat_id:'erased',payload:{text:'Erasure response delivered.'}}:{})}});
        sent++;
      }catch{
        failed++;const attempts=(item.attempts||0)+1;
        await db.request(filter,{method:'PATCH',body:{state:attempts>=20?'failed':'pending',attempts,
          next_attempt_at:new Date(now()+Math.min(3600000,60000*2**Math.min(attempts,6))).toISOString(),lease_owner:null,lease_until:null}});
      }
      await sleep(1100);
    }
  }finally{
    await db.request(`privacy_outbox?lease_owner=eq.${owner}&state=eq.pending`,{method:'PATCH',body:{lease_owner:null,lease_until:null}});
  }
  return {sent,failed};
}

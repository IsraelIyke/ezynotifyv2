import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createPrivacyAdmin,deliverPrivacyNotifications} from '../supabase/functions/_shared/privacy-admin.mjs';

function fixture(){
  const messages=[],actions=[],sessions=new Map(),audit=[];let uid=0;
  const row={id:2,chat_id:'123',kind:'deletion',status:'pending',created_at:'2026-10-16T19:04:17Z',active_erased_at:null};
  const db={async request(path,options){
    if(path.startsWith('privacy_admin_settings'))return [{enabled:true,chat_id:'999',admin_user_id:'999'}];
    if(path.startsWith('privacy_requests'))return [row];
    if(path.startsWith('privacy_audit'))return audit;
    if(path.startsWith('privacy_admin_sessions')){
      if(options?.method==='POST'){sessions.set(options.body.admin_id,options.body);return;}
      if(options?.method==='DELETE'){sessions.clear();return;}
      return [...sessions.values()];
    }
    return [];
  },async rpc(name,args){
    actions.push([name,args]);
    if(args.p_action==='preview')return {account:'123',token:'safe-preview-token',counts:{monitors:3,profile:1}};
    if(args.p_action==='erase'){row.active_erased_at='2026-10-17';return {erased:true};}
    return {ok:true};
  }};
  const bot=createPrivacyAdmin({db,telegram:async(method,body)=>{messages.push([method,body]);return {};}});
  const click=(data,actor=999)=>bot({update_id:++uid,callback_query:{id:String(uid),from:{id:actor},message:{message_id:10,chat:{id:actor,type:'private'}},data}});
  const send=text=>bot({update_id:++uid,message:{from:{id:999},chat:{id:999,type:'private'},text}});
  return {click,send,messages,actions,sessions,row,audit};
}

test('prepared access outcome requires an export and explicit confirmation without typing',async()=>{
  const f=fixture();f.row.kind='access';
  await f.click('pv:outcome:2');assert.ok(f.messages.at(-1)[1].text.includes('export first'));
  assert.equal(f.sessions.size,0);
  f.audit.push({action:'export'});await f.click('pv:outcome:2');
  assert.ok(!f.actions.some(([,a])=>a.p_action==='complete'));
  const confirm=f.messages.at(-1)[1].reply_markup.inline_keyboard[0][0].callback_data;
  await f.click(confirm);assert.equal(f.actions.at(-1)[1].p_action,'complete');
  assert.ok(f.actions.at(-1)[1].p_note.includes('account/settings export'));
});

test('deletion checklist uses a confirmed statement and does not silently finish a request',async()=>{
  const f=fixture();await f.click('pv:outcome:2');assert.equal(f.sessions.size,0);
  await f.click('pv:nocopies:2');assert.equal(f.sessions.size,0);
  f.row.active_erased_at='2026-10-17';await f.click('pv:nocopies:2');
  assert.ok(!f.actions.some(([,a])=>a.p_action==='retention'));
  await f.click(f.messages.at(-1)[1].reply_markup.inline_keyboard[0][0].callback_data);
  assert.equal(f.actions.at(-1)[1].p_action,'retention');
  assert.ok(!f.actions.some(([,a])=>a.p_action==='complete'));
  f.row.external_review_note='Confirmed handling';await f.click('pv:outcome:2');
  assert.ok(f.messages.at(-1)[1].text.includes('active EzyNotify account data'));
});

test('request view gives a next step and hides premature deletion completion',async()=>{
  const f=fixture();await f.click('pv:view:2');
  assert.ok(f.messages.at(-1)[1].text.includes('Next: delete'));
  assert.ok(!f.messages.at(-1)[1].reply_markup.inline_keyboard.flat().some(b=>b.callback_data==='pv:outcome:2'));
  f.row.active_erased_at='2026-10-17';await f.click('pv:view:2');
  assert.ok(f.messages.at(-1)[1].reply_markup.inline_keyboard.flat().some(b=>b.callback_data==='pv:handling:2'));
});

test('unauthorized actors cannot view requests or invoke actions',async()=>{
  const f=fixture();await f.click('pv:preview:2',123);
  assert.equal(f.messages.length,0);assert.equal(f.actions.length,0);
});
test('opening a request is passive, deletion requires preview then explicit confirmation',async()=>{
  const f=fixture();await f.click('pv:view:2');assert.equal(f.actions.length,0);
  await f.click('pv:preview:2');assert.equal(f.actions.at(-1)[1].p_action,'preview');
  const body=f.messages.at(-1)[1];assert.ok(body.text.includes('Account: <code>123</code>'));
  assert.ok(body.reply_markup.inline_keyboard.flat().some(b=>b.callback_data==='pv:erase:2:safe-preview-token'));
  assert.ok(!f.actions.some(([,a])=>a.p_action==='erase'));
  await f.click('pv:erase:2:safe-preview-token');assert.equal(f.actions.at(-1)[1].p_action,'erase');
  assert.ok(f.messages.at(-1)[1].text.includes('Active user data erased'));
});
test('manual replies queue the typed response and completion requires its own confirmation',async()=>{
  const f=fixture();await f.click('pv:reply:2');await f.send('Please clarify which records you mean.');
  assert.equal(f.actions.at(-1)[1].p_action,'reply');assert.ok(f.actions.at(-1)[1].p_note.includes('clarify'));
  await f.click('pv:complete:2');await f.send('Here is the actual outcome of your request.');
  assert.ok(!f.actions.some(([,a])=>a.p_action==='complete'));
  const confirm=f.messages.at(-1)[1].reply_markup.inline_keyboard[0][0].callback_data;
  await f.click('pv:confirm:2:invalid');assert.ok(!f.actions.some(([,a])=>a.p_action==='complete'));
  await f.click(confirm);assert.equal(f.actions.at(-1)[1].p_action,'complete');
});
test('privacy delivery routes admin requests to the privacy bot and user responses to the main bot',async()=>{
  const routed=[],patches=[];
  const rows=[{id:1,request_id:2,destination:'admin',chat_id:'999',payload:{text:'Request'}},{id:2,request_id:2,destination:'user',chat_id:'123',payload:{text:'Erasure outcome',_erase_receipt:true}}];
  const db={rpc:async()=>rows,request:async(path,options)=>{if(options)patches.push(options.body);else return [{status:'completed',active_erased_at:'2026-10-17'}];}};
  const result=await deliverPrivacyNotifications({db,adminTelegram:async(method,body)=>routed.push(['privacy',body]),userTelegram:async(method,body)=>routed.push(['main',body]),sleep:async()=>{}});
  assert.deepEqual(result,{sent:2,failed:0});assert.equal(routed[0][0],'privacy');assert.equal(routed[1][0],'main');
  assert.ok(!('_erase_receipt' in routed[1][1]));assert.equal(patches[1].chat_id,'erased');
});
test('failed delivery remains queued and account exports use the file sender',async()=>{
  const patches=[],files=[];let rows=[{id:1,request_id:2,destination:'user',chat_id:'123',payload:{text:'Export',_document_json:{profile:{chat_id:'123'}}}}];
  const db={rpc:async()=>rows,request:async(path,options)=>{if(options)patches.push(options.body);else return [{status:'pending'}];}};
  const base={db,adminTelegram:async()=>{},userTelegram:async()=>{throw Error('private');},sleep:async()=>{}};
  assert.deepEqual(await deliverPrivacyNotifications({...base,sendFile:async(...args)=>files.push(args)}),{sent:1,failed:0});
  assert.equal(files[0][0],'123');assert.equal(JSON.parse(files[0][1]).profile.chat_id,'123');
  rows=[{...rows[0],payload:{text:'Reply'}}];
  assert.deepEqual(await deliverPrivacyNotifications(base),{sent:0,failed:1});
  assert.ok(patches.some(p=>p.state==='pending'&&p.attempts===1));
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBot, normalizeUrl } from '../supabase/functions/_shared/bot.mjs';
import { dispatchChecks, secureEquals } from '../supabase/functions/_shared/services.mjs';

test('privacy command and request buttons are reachable',async()=>{
  const f=fixture();await f.send('/privacy');
  assert.ok(f.messages.at(-1).body.text.includes('robots.txt'));
  await f.click('privacy:1');assert.ok(f.messages.at(-1).body.text.includes('Every failed check'));
  await f.click('privacy:deletion');assert.ok(f.calls.some(([name,args])=>name==='request_bot_privacy'&&args.p_chat==='123'&&args.p_kind==='deletion'));
});

test('health monitor creation skips keywords and AI settings and survives edits',async()=>{
  const f=fixture();await f.send('/new');
  assert.ok(f.messages.at(-1).body.reply_markup.inline_keyboard.flat().some(b=>b.callback_data==='new:health'));
  await f.click('new:health');await f.send('https://example.com');await f.send('My website');
  const d=f.sessions.get('123').draft;assert.equal(d.step,'interval');
  await f.click(`draft:${d.nonce}:interval-10`);assert.equal(f.sessions.get('123').draft.step,'review');
  await f.click(`draft:${d.nonce}:confirm`);
  const m=f.monitors.get('1');assert.equal(m.fetch_config.mode,'health');assert.equal(m.keywords,null);
  await f.click('edit:1');assert.equal(f.sessions.get('123').draft.fetch_config.mode,'health');
  assert.ok(!f.messages.at(-1).body.reply_markup.inline_keyboard.flat().some(b=>/advanced|keywords|repeat/.test(b.callback_data)));
});

function fixture() {
  const sessions=new Map(), monitors=new Map(), preferences=new Map(), calls=[], messages=[];
  let clock=new Date('2026-10-16T12:00:00Z');
  const db={
    async request(path,options={}) {
      calls.push([path,options]);
      const [table,query='']=path.split('?'),params=new URLSearchParams(query);
      if(table==='bot_preferences') {
        if(options.method==='POST'){preferences.set(options.body.chat_id,options.body);return null;}
        const pref=preferences.get(params.get('chat_id')?.slice(3));return pref?[pref]:[];
      }
      if(table==='bot_sessions') {
        const chat=params.get('chat_id')?.slice(3);
        if(options.method==='POST'){sessions.set(options.body.chat_id,structuredClone(options.body));return null;}
        if(options.method==='DELETE'){sessions.delete(chat);return null;}
        const s=sessions.get(chat);return s&&new Date(s.expires_at)>clock?[{draft:structuredClone(s.draft)}]:[];
      }
      if(table==='ezynotify') {
        let rows=[...monitors.values()].filter(m=>!params.has('telegramID')||String(m.telegramID)===params.get('telegramID').slice(3));
        if(params.has('id'))rows=rows.filter(m=>String(m.id)===params.get('id').slice(3));
        if(options.method==='DELETE'){rows.forEach(m=>monitors.delete(String(m.id)));return null;}
        if(options.method==='PATCH'){rows.forEach(m=>Object.assign(m,options.body));return null;}
        return structuredClone(rows);
      }
      return [];
    },
    async rpc(name,body){
      calls.push([name,body]);
      if(name==='commit_bot_draft'){
        const d=sessions.get(body.p_chat);
        assert.equal(d.draft.nonce,body.p_nonce);
        const id=body.p_id||String(monitors.size+1);
        monitors.set(id,{id,telegramID:body.p_chat,...body.p_fields});sessions.delete(body.p_chat);return id;
      }
      if(name==='request_monitor_check')return 'queued';
      return true;
    },
  };
  const telegram=async(method,body)=>{messages.push({method,body});return {};};
  let bot=createBot({db,telegram,dispatch:async()=>({status:'dispatched'}),now:()=>clock});
  let updateId=0;
  const send=async(text)=>bot({update_id:++updateId,message:{chat:{id:123,type:'private'},from:{id:123},text}});
  const click=async(data,user=123)=>bot({update_id:++updateId,callback_query:{id:String(updateId),from:{id:user},message:{chat:{id:user,type:'private'},message_id:4},data}});
  return {sessions,monitors,calls,messages,send,click,restart(){bot=createBot({db,telegram,dispatch:async()=>{},now:()=>clock});},advance(ms){clock=new Date(clock.getTime()+ms);}};
}

test('complete creation survives function restarts and saves only after confirmation',async()=>{
  const f=fixture();
  await f.send('/new_update_monitor');await f.send('https://example.com/news');
  f.restart();await f.send('Admissions');
  let d=f.sessions.get('123').draft;
  await f.click(`draft:${d.nonce}:interval-30`);await f.click(`draft:${d.nonce}:repeat-yes`);await f.click(`draft:${d.nonce}:detail-yes`);
  assert.equal(f.monitors.size,0);
  await f.click(`draft:${d.nonce}:confirm`);
  assert.equal(f.monitors.size,1);assert.equal(f.monitors.get('1').interval_minutes,30);
  assert.ok(!f.messages.some(m=>/groq/i.test(m.body.text||'')));
  assert.ok(!f.messages.some(m=>(m.body.text||'').includes('\u2014')));
  await f.click(`draft:${d.nonce}:confirm`);assert.equal(f.monitors.size,1);
});

test('cancel during an edit never deletes or mutates the saved monitor',async()=>{
  const f=fixture();f.monitors.set('7',{id:7,telegramID:'123',url:'https://example.com',title:'Original',checkUpdates:true,fetch_config:{mode:'browser'}});
  await f.click('edit:7');const d=f.sessions.get('123').draft;
  await f.click(`draft:${d.nonce}:edit-title`);await f.send('New title');await f.send('/cancel');
  assert.equal(f.monitors.get('7').title,'Original');assert.equal(f.monitors.size,1);
  assert.ok(!f.calls.some(([path,o])=>path.startsWith('ezynotify')&&['PATCH','DELETE'].includes(o.method)));
});

test('callbacks cannot access another chat’s monitor',async()=>{
  const f=fixture();f.monitors.set('1',{id:1,telegramID:'123',url:'https://example.com'});
  for(const verb of ['view','edit','toggle','remove','check','history'])await f.click(`${verb}:1`,456);
  assert.ok(!f.calls.some(([p,o])=>p==='request_monitor_check'||p.startsWith('monitor_events')||o.method==='PATCH'||o.method==='DELETE'));
  assert.equal(f.monitors.size,1);
});

test('expired and replaced draft buttons cannot save',async()=>{
  const f=fixture();await f.send('/new_update_monitor');const nonce=f.sessions.get('123').draft.nonce;
  await f.send('/new_keyword_check');await f.click(`draft:${nonce}:confirm`);
  f.advance(31*60000);await f.click(`draft:${f.sessions.get('123').draft.nonce}:confirm`);
  assert.equal(f.monitors.size,0);
});

test('user text is HTML escaped and destructive actions require confirmation',async()=>{
  const f=fixture();f.monitors.set('1',{id:1,telegramID:'123',url:'https://example.com',title:'<script>&',fetch_config:{}});
  await f.click('delete:1');assert.equal(f.monitors.size,1);
  assert.match(f.messages.at(-1).body.text,/&lt;script&gt;&amp;/);
  assert.ok(f.messages.at(-1).body.reply_markup.inline_keyboard[0][0].style==='danger');
  await f.click('remove:1');assert.equal(f.monitors.size,0);
});

test('public URL validation rejects credentials, private names, literal IPs and unsafe schemes',()=>{
  for(const value of ['http://127.0.0.1','http://2130706433','http://localhost','http://service.internal','http://user:pass@example.com','https://example.com:8080','file:///etc/passwd','http://[::1]'])assert.throws(()=>normalizeUrl(value),undefined,value);
  assert.equal(normalizeUrl('example.com/news#section'),'https://example.com/news');
});

test('timezone survives handler restarts and applies to monitor times with readable intervals',async()=>{
  const f=fixture();
  await f.send('/timezone Africa/Lagos');f.restart();
  f.monitors.set('1',{id:1,telegramID:'123',url:'https://example.com',title:'News',checkUpdates:true,interval_minutes:1440,last_checked_at:'2026-10-16T12:00:00Z',fetch_config:{}});
  await f.click('view:1');
  const text=f.messages.at(-1).body.text;
  assert.match(text,/Every 1 day/);assert.match(text,/13:00/);assert.match(text,/Africa\/Lagos/);assert.ok(!text.includes('1440 minutes'));
  await f.send('/timezone Invalid/Timezone');assert.match(f.messages.at(-1).body.text,/not recognized/);
});

test('dispatch is authenticated, coalesced, and does not start an idle batch',async()=>{
  assert.equal(await secureEquals('secret','secret'),true);assert.equal(await secureEquals('secret','other'),false);assert.equal(await secureEquals(null,'secret'),false);
  let requests=0;const env=n=>({GITHUB_REPOSITORY:'IsraelIyke/ezynotifyv2',GITHUB_DISPATCH_TOKEN:'offline-token',GITHUB_REF:'test-branch'}[n]);
  const fetcher=async(url,options)=>{requests++;assert.match(url,/ezynotify.yml\/dispatches$/);assert.equal(JSON.parse(options.body).ref,'test-branch');return {ok:true};};
  const db={request:async path=>path.startsWith('ezynotify')?[{id:1}]:[],rpc:async()=>true};
  assert.equal((await dispatchChecks(db,env,fetcher)).status,'dispatched');assert.equal(requests,1);
  db.rpc=async()=>false;assert.equal((await dispatchChecks(db,env,fetcher)).status,'queued');assert.equal(requests,1);
  db.request=async()=>[];assert.equal((await dispatchChecks(db,env,fetcher)).status,'idle');assert.equal(requests,1);
});

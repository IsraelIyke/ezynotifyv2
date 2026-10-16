import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deliverAdminNotifications } from '../supabase/functions/_shared/admin-notifications.mjs';
import { PRIVACY_PAGES } from '../supabase/functions/_shared/privacy.mjs';

test('admin sender delivers every failure independently and strips internal metadata',async()=>{
  const sent=[],patches=[];
  const db={rpc:async()=>[1,2].map(id=>({id,chat_id:'999',payload:{text:'Failure',_checked_at:'2026-10-16T12:00:00Z'}})),request:async(path,opts)=>{if(!opts)return [{timezone:'Africa/Lagos'}];patches.push(opts.body);}};
  assert.deepEqual(await deliverAdminNotifications({db,telegram:async(method,body)=>sent.push(body),sleep:async()=>{}}),{sent:2,failed:0});
  assert.equal(sent.length,2);assert.equal(sent[0].chat_id,'999');assert.ok(!('_checked_at' in sent[0]));
  assert.equal(patches.filter(x=>x.state==='sent').length,2);
});
test('delivery failures retry without exposing errors or losing the pending alert',async()=>{
  const patches=[];
  const db={rpc:async()=>[{id:1,chat_id:'999',attempts:0,payload:{text:'Failure'}}],request:async(path,opts)=>{if(!opts)return [];patches.push(opts.body);}};
  assert.deepEqual(await deliverAdminNotifications({db,telegram:async()=>{throw Error('secret');},sleep:async()=>{}}),{sent:0,failed:1});
  assert.equal(patches[0].state,'pending');assert.equal(patches[0].attempts,1);assert.ok(!JSON.stringify(patches).includes('secret'));
});
test('privacy notice fits Telegram and explains robots and operator diagnostics',()=>{
  for(const page of PRIVACY_PAGES)assert.ok(page.length<4096);
  assert.ok(PRIVACY_PAGES[0].includes('robots.txt'));assert.ok(PRIVACY_PAGES[1].includes('Every failed check'));
});

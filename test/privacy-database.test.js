import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';

test('privacy requests authorize the operator and require confirmed, isolated erasure',async t=>{
  const db=new PGlite();
  try{
    await db.exec('create role anon;create role authenticated;create role service_role;');
    await db.exec(await readFile(new URL('../supabase/schema.sql',import.meta.url),'utf8'));
    for(const name of (await readdir(new URL('../supabase/migrations/',import.meta.url))).filter(n=>n.endsWith('.sql')).sort())
      await db.exec(await readFile(new URL('../supabase/migrations/'+name,import.meta.url),'utf8'));
    await db.exec("insert into privacy_admin_settings(chat_id,admin_user_id,enabled) values('999','999',true);");
    await db.exec(`insert into bot_users(chat_id,first_name) values('123','Delete me'),('456','Keep me');
      insert into bot_preferences(chat_id) values('123');
      insert into bot_sessions(chat_id,draft) values('123','{}');
      insert into ezynotify(url,"telegramID",reference) values('https://example.com/private','123','User snapshot'),('https://other.test','456','Keep snapshot');
      insert into monitor_events(monitor_id,status,summary) values('1','baseline','User content');
      insert into notification_outbox(monitor_id,chat_id,payload) values('1','123','{"text":"Private image","_photo":"data"}');
      insert into fetch_cache(cache_key,result) values('unlinked','{"text":"Cached snapshot"}');`);
    const id=(await db.query("select request_bot_privacy('123','deletion') id")).rows[0].id;
    const action=async(name,note='',token='',actor='999',event='')=>(await db.query('select privacy_action($1,$2,$3,$4,$5,$6) result',[id,actor,name,note,token,event])).rows[0].result;
    await t.test('new requests route to privacy outbox and a duplicate reuses the request',async()=>{
      assert.equal((await db.query("select request_bot_privacy('123','deletion') id")).rows[0].id,id);
      const queued=(await db.query('select * from privacy_outbox')).rows;
      assert.equal(queued.length,1);assert.equal(queued[0].destination,'admin');assert.equal(queued[0].chat_id,'999');
      await assert.rejects(action('preview','','','attacker'));
    });
    let token;
    await t.test('preview is optional before manual review and never erases anything',async()=>{
      const preview=await action('preview');token=preview.token;
      assert.equal(preview.counts.monitors,1);assert.equal(preview.account,'123');
      assert.equal((await db.query("select count(*)::int n from ezynotify where \"telegramID\"='123'")).rows[0].n,1);
      assert.equal((await action('erase','','wrong-token')).error,'expired_confirmation');
      await db.query("update privacy_outbox set lease_owner='privacy-sender',lease_until=now()+interval '2 minutes' where request_id=$1",[id]);
      assert.equal((await action('erase','',token)).queued,true);
      assert.equal((await db.query('select erase_queued_at is not null queued from privacy_requests where id=$1',[id])).rows[0].queued,true);
      assert.equal((await action('cancel_erasure')).cancelled,true);
      assert.equal((await db.query('select erase_token from privacy_requests where id=$1',[id])).rows[0].erase_token,null);
      token=(await action('preview')).token;
      await db.exec('update privacy_outbox set lease_owner=null,lease_until=null');
      await db.exec("insert into runtime_locks(name,owner,expires_at) values('worker','running-worker',now()+interval '1 hour')");
      assert.equal((await action('erase','','wrong-token')).error,'expired_confirmation');
      assert.equal((await action('erase','',token)).queued,true);
      assert.equal((await db.query('select process_privacy_erasures() result')).rows[0].result.waiting,1);
      await db.exec("delete from runtime_locks where name='worker'");
    });
    await t.test('confirmed erasure removes active records but preserves other users',async()=>{
      const result=(await db.query('select process_privacy_erasures() result')).rows[0].result;assert.equal(result.erased,1);
      assert.equal((await db.query('select process_privacy_erasures() result')).rows[0].result.erased,0);
      for(const table of ['bot_users','bot_preferences','bot_sessions'])assert.equal((await db.query(`select count(*)::int n from ${table} where chat_id='123'`)).rows[0].n,0);
      assert.equal((await db.query('select count(*)::int n from monitor_events')).rows[0].n,0);
      assert.equal((await db.query('select count(*)::int n from notification_outbox')).rows[0].n,0);
      assert.equal((await db.query('select count(*)::int n from fetch_cache')).rows[0].n,0);
      assert.equal((await db.query("select reference from ezynotify where \"telegramID\"='456'")).rows[0].reference,'Keep snapshot');
      assert.equal((await action('erase','',token)).error,'already_erased');
      assert.equal((await action('complete','Active account deleted.')).error,'external_review_required');
    });
    await t.test('completion requires external-handling note and unlinks the requester',async()=>{
      await db.exec("insert into bot_users(chat_id,first_name) values('123','Returned to service')");
      await action('retention','Backups have been assessed and restricted from use until expiry.');
      assert.equal((await action('complete','The original active account records were removed.')).error,'new_activity');
      const renewed=await action('preview');assert.equal(renewed.counts.profile,1);
      assert.equal((await action('erase','',renewed.token)).erased,true);
      await action('retention','Private backup copies scheduled for deletion on 20 October; restricted from operational use.');
      assert.equal((await action('complete','Your active account records and monitors have been erased.')).ok,true);
      const request=(await db.query('select * from privacy_requests where id=$1',[id])).rows[0];
      assert.equal(request.status,'completed');assert.ok(request.chat_id.startsWith('erased:'));
      assert.equal(request.external_review_note,null);
      const pending=(await db.query("select * from privacy_outbox where state='pending'")).rows;
      assert.equal(pending.length,1);assert.equal(pending[0].destination,'user');assert.equal(pending[0].payload._erase_receipt,true);
    });
    await t.test('access exports and corrections are scoped to the requester and replies are ownership checked',async()=>{
      const access=(await db.query("select request_bot_privacy('456','access') id")).rows[0].id;
      await db.query("select privacy_action($1,'999','export','','','export-update')",[access]);
      const exported=(await db.query("select payload from privacy_outbox where request_id=$1 and destination='user'",[access])).rows[0].payload._document_json;
      assert.equal(exported.profile.chat_id,'456');assert.equal(exported.monitors.length,1);assert.equal(exported.monitors[0].url,'https://other.test');
      const correction=(await db.query("select request_bot_privacy('456','correction') id")).rows[0].id;
      const patch=JSON.stringify({first_name:'Corrected name',timezone:'Africa/Lagos'});
      await db.query("select privacy_action($1,'999','correct',$2,'','correction-update')",[correction,patch]);
      assert.equal((await db.query("select first_name from bot_users where chat_id='456'")).rows[0].first_name,'Corrected name');
      assert.equal((await db.query("select timezone from bot_preferences where chat_id='456'")).rows[0].timezone,'Africa/Lagos');
      assert.equal((await db.query("select privacy_user_note($1,'123','Forged message') saved",[correction])).rows[0].saved,false);
      assert.equal((await db.query("select privacy_user_note($1,'456','Please correct <this>') saved",[correction])).rows[0].saved,true);
      assert.ok((await db.query("select payload from privacy_outbox where request_id=$1 and destination='admin' order by id desc limit 1",[correction])).rows[0].payload.text.includes('&lt;this&gt;'));
      assert.equal((await db.query("select privacy_action($1,'999','correct','{\"is_admin\":\"true\"}','','') result",[correction])).rows[0].result.error,'invalid_fields');
    });
    await t.test('expired or replaced queued consent cannot erase data',async()=>{
      await db.exec("insert into bot_users(chat_id) values('789');insert into runtime_locks(name,owner,expires_at) values('worker','test-worker',now()+interval '1 hour')");
      const queuedId=(await db.query("select request_bot_privacy('789','deletion') id")).rows[0].id;
      const apply=async(action,token='')=>(await db.query("select privacy_action($1,'999',$2,'',$3,'') result",[queuedId,action,token])).rows[0].result;
      let preview=await apply('preview');assert.equal((await apply('erase',preview.token)).queued,true);
      await db.query("update privacy_requests set erase_expires_at=now()-interval '1 second' where id=$1",[queuedId]);
      await db.query('select process_privacy_erasures()');
      let state=(await db.query('select erase_queued_at,active_erased_at from privacy_requests where id=$1',[queuedId])).rows[0];
      assert.equal(state.erase_queued_at,null);assert.equal(state.active_erased_at,null);
      preview=await apply('preview');assert.equal((await apply('erase',preview.token)).queued,true);
      await apply('preview');
      assert.equal((await db.query('select process_privacy_erasures() result')).rows[0].result.erased,0);
      assert.equal((await db.query("select count(*)::int n from bot_users where chat_id='789'")).rows[0].n,1);
      await db.exec("delete from runtime_locks where name='worker'");
    });
    await t.test('public roles cannot invoke erasure or read admin tables',async()=>{
      await db.exec('set role anon');
      await assert.rejects(db.query("select privacy_action(1,'999','erase','','','')"));
      await assert.rejects(db.query('select process_privacy_erasures()'));
      await assert.rejects(db.query('select * from privacy_admin_settings'));
      await db.exec('reset role');
    });
  }finally{await db.close();}
});

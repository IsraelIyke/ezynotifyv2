import os
import unittest
from unittest.mock import patch
from monitor.worker import deliver_notifications
from monitor.processor import alert


class Response:
    status=200
    ok=True
    async def json(self):return {'ok':True}
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass


class Session:
    def __init__(self):self.urls=[];self.payloads=[]
    def post(self,url,**kwargs):self.urls.append(url);self.payloads.append(kwargs.get('json',kwargs.get('data')));return Response()


class Store:
    def __init__(self,channels):
        self.items=[{'id':i,'monitor_id':str(i),'chat_id':'123','channel':channel,'payload':{'text':'Test'},'attempts':0} for i,channel in enumerate(channels,1)]
        self.patches=[]
    async def rpc(self,*args):return self.items
    async def request(self,path,method='GET',body=None):
        if method=='GET':return [{'id':1,'paused':False}]
        self.patches.append(body)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_screenshot_is_uploaded_as_photo_and_remains_separate_from_text(self):
        import base64
        store=Store(['keywords']);session=Session()
        store.items[0]['payload']['_photo']=base64.b64encode(b'jpeg-fixture').decode()
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token'}):
            failed=await deliver_notifications(store,session,'offline-owner')
        self.assertEqual(failed,0);self.assertTrue(session.urls[0].endswith('/sendPhoto'))
        fields={field[0]['name']:field[2] for field in session.payloads[0]._fields}
        self.assertEqual(fields['photo'],b'jpeg-fixture');self.assertNotIn('_photo',fields)
        self.assertEqual(store.patches[0]['state'],'sent')

    async def test_health_notifications_use_main_bot_and_keep_only_the_health_label(self):
        store=Store(['health']);session=Session()
        store.items[0]['payload']['text']='<b>🌐 WEBSITE HEALTH</b>\nWebsite unavailable'
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token'}):await deliver_notifications(store,session,'offline-owner')
        self.assertIn('main-offline-token',session.urls[0])
        self.assertTrue(session.payloads[0]['text'].startswith('<b>🌐 WEBSITE HEALTH</b>'))
        self.assertNotIn('KEYWORD WATCH',session.payloads[0]['text'])

    async def test_auto_pause_notice_is_delivered_while_other_paused_alerts_are_cancelled(self):
        store=Store(['updates','updates']);session=Session()
        store.items[0]['payload']['_auto_paused']=True
        request=store.request
        async def paused(path,method='GET',body=None):
            if path.startswith('ezynotify?'):return [{'id':1,'paused':True,'failure_count':5}]
            return await request(path,method,body)
        store.request=paused
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token'}):await deliver_notifications(store,session,'offline-owner')
        self.assertEqual(len(session.payloads),1);self.assertNotIn('_auto_paused',session.payloads[0])
        self.assertEqual([p['state'] for p in store.patches],['sent','cancelled'])

    async def test_resume_cancels_stale_auto_pause_notice(self):
        store=Store(['updates']);session=Session();store.items[0]['payload']['_auto_paused']=True
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token'}):await deliver_notifications(store,session,'offline-owner')
        self.assertEqual(session.payloads,[]);self.assertEqual(store.patches[0]['state'],'cancelled')

    async def test_every_category_uses_the_main_bot_even_when_legacy_tokens_exist(self):
        store=Store(['keywords','updates','main']);session=Session()
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token','TELEGRAM_BOT_TOKEN':'unused-keyword-token','TELEGRAM_UPDATES_BOT_TOKEN':'unused-update-token'}):
            failed=await deliver_notifications(store,session,'offline-owner')
        self.assertEqual(failed,0)
        self.assertEqual(session.urls,['https://api.telegram.org/botmain-offline-token/sendMessage']*3)
        self.assertTrue(all(p['state']=='sent' for p in store.patches))
        self.assertTrue(session.payloads[0]['text'].startswith('<b>🔎 KEYWORD WATCH</b>'))
        self.assertTrue(session.payloads[1]['text'].startswith('<b>🔄 PAGE UPDATES</b>'))

    async def test_missing_main_token_keeps_alerts_queued_and_never_uses_an_old_bot(self):
        store=Store(['keywords','updates']);session=Session()
        with patch.dict(os.environ,{'BOT_TOKEN':'','TELEGRAM_BOT_TOKEN':'unused-keyword-token','TELEGRAM_UPDATES_BOT_TOKEN':'unused-update-token'}):
            failed=await deliver_notifications(store,session,'offline-owner')
        self.assertEqual(failed,2);self.assertEqual(session.urls,[])
        self.assertTrue(all(p['state']=='pending' for p in store.patches))

    async def test_every_notice_has_a_monitor_type_label(self):
        for updates,label in [(True,'🔄 PAGE UPDATES'),(False,'🔎 KEYWORD WATCH')]:
            row={'url':'https://example.com','title':'Test','checkUpdates':updates}
            for heading in ['Keyword match','Website changed','Check unavailable','Monitoring recovered']:
                result=alert(row,heading,'Notice')
                self.assertTrue(result['payload']['text'].startswith(f'<b>{label}</b>'))

    async def test_alert_timestamp_uses_saved_timezone_and_metadata_is_not_sent_to_telegram(self):
        store=Store(['updates']);session=Session()
        store.items[0]['payload']['_checked_at']='2026-10-16T12:00:00Z'
        request=store.request
        async def preferences(path,method='GET',body=None):
            if path.startswith('bot_preferences'):return [{'timezone':'Africa/Lagos'}]
            return await request(path,method,body)
        store.request=preferences
        with patch.dict(os.environ,{'BOT_TOKEN':'main-offline-token'}):await deliver_notifications(store,session,'offline-owner')
        self.assertIn('13:00',session.payloads[0]['text']);self.assertIn('Africa/Lagos',session.payloads[0]['text'])
        self.assertNotIn('_checked_at',session.payloads[0])


if __name__=='__main__':unittest.main()

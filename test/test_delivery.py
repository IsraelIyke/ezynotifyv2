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
    def post(self,url,**kwargs):self.urls.append(url);self.payloads.append(kwargs['json']);return Response()


class Store:
    def __init__(self,channels):
        self.items=[{'id':i,'monitor_id':str(i),'chat_id':'123','channel':channel,'payload':{'text':'Test'},'attempts':0} for i,channel in enumerate(channels,1)]
        self.patches=[]
    async def rpc(self,*args):return self.items
    async def request(self,path,method='GET',body=None):
        if method=='GET':return [{'id':1,'paused':False}]
        self.patches.append(body)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
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

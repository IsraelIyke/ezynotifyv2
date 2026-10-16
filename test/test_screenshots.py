import base64
import unittest
from unittest.mock import AsyncMock,Mock
from test_browser import FixtureFetcher
from monitor.models import FetchResult
from monitor.processor import process


class ScreenshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_browser_captures_highlighted_keyword_without_desktop(self):
        row={'url':'https://fixture.test/normal','keywords':{'keywords':['welcome']},'fetch_config':{'screenshot':True}}
        async with FixtureFetcher() as fetcher:
            result=await fetcher.fetch(row['url'],{'mode':'browser','selector':'main','screenshot':True,'_monitor':row})
        self.assertEqual(result.status,'ok')
        capture=result.alert_images['keywords']
        self.assertIn('highlighted',capture['note'])
        self.assertTrue(base64.b64decode(capture['image']).startswith(b'\xff\xd8'))
        self.assertNotIn('alert_images',result.cached())
        _,_,notices=await process(row,result,AsyncMock())
        self.assertEqual(len(notices),2);self.assertIn('_photo',notices[-1]['payload'])

    async def test_deletion_shows_current_area_and_baseline_sends_no_image(self):
        async with FixtureFetcher() as fetcher:
            config={'mode':'browser','selector':'main','ignore':'.clock','screenshot':True,'_monitor':{'checkUpdates':True,'reference':'Welcome yesterday'}}
            result=await fetcher.fetch('https://fixture.test/normal',config)
            self.assertEqual(result.text,'Welcome')
            self.assertIn('removed text',result.alert_images['updates']['note'])
            config['_monitor']['reference']=''
            result=await fetcher.fetch('https://fixture.test/normal',config)
            self.assertEqual(result.alert_images,{})

    async def test_screenshot_error_preserves_success_and_reports_text_fallback(self):
        from monitor.screenshots import capture_alert_images
        target=Mock();target.get_by_text.side_effect=RuntimeError('No image')
        row={'url':'https://fixture.test/normal','keywords':{'keywords':['welcome']},'fetch_config':{'screenshot':True}}
        images=await capture_alert_images(AsyncMock(),target,'Welcome',{'screenshot':True,'_monitor':row})
        self.assertEqual(images,{})
        update,_,notices=await process(row,FetchResult('ok',text='Welcome'),AsyncMock())
        self.assertEqual(update['failure_count'],0)
        self.assertIn('Screenshot unavailable',notices[0]['payload']['text'])

import sys
import unittest
from unittest.mock import patch
from test_browser import FixtureFetcher
from monitor.desktop import DesktopUnavailable


@unittest.skipUnless(sys.platform=='win32','Requires an interactive Windows desktop')
class DesktopTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_desktop_navigation_scroll_and_extraction(self):
        async with FixtureFetcher() as fetcher:
            result=await fetcher.fetch_advanced('https://fixture.test/normal?q=%25+(test)',{'selector':'main','ignore':'.clock'})
            self.assertEqual(result.status,'ok',result.reason)
            self.assertEqual(result.text,'Welcome')

    async def test_desktop_failure_does_not_become_website_failure(self):
        async with FixtureFetcher() as fetcher:
            with patch('monitor.desktop.type_url',side_effect=DesktopUnavailable('No desktop')):
                with self.assertRaises(DesktopUnavailable):
                    await fetcher.fetch_advanced('https://fixture.test/normal',{})

    async def test_advanced_still_rejects_private_redirects(self):
        async with FixtureFetcher() as fetcher:
            result=await fetcher.fetch_advanced('https://fixture.test/redirect',{})
            self.assertEqual(result.status,'unsafe_url')

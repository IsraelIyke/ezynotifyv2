"""Navigate an isolated Chromium window through the Windows desktop."""
import asyncio
import re
import uuid


class DesktopUnavailable(RuntimeError):
    pass


def type_url(title,url):
    try:
        from pywinauto import Desktop
        window=Desktop(backend='win32').window(title_re=re.escape(title)+'.*',class_name='Chrome_WidgetWin_1')
        window.wait('visible',timeout=10)
        window.set_focus()
        window.type_keys('^l',pause=0.05)
        # URLs may contain keyboard metacharacters; always type them literally.
        literal=''.join('{'+c+'}' if c in '{}+^%~()' else c for c in url)
        window.type_keys(literal,with_spaces=True,pause=0.001)
        window.type_keys('{ENTER}')
    except Exception as error:
        raise DesktopUnavailable('The Windows desktop could not accept browser input.') from error


async def navigate_desktop(page,url):
    title='EzyNotify-'+uuid.uuid4().hex
    await page.evaluate('(title) => document.title=title',title)
    responses=[]
    def response_received(response):
        if response.request.is_navigation_request() and response.frame==page.main_frame:
            responses.append(response)
    page.on('response',response_received)
    try:
        await asyncio.to_thread(type_url,title,url)
        await page.wait_for_url(lambda value:value!='about:blank',wait_until='domcontentloaded',timeout=30000)
        return responses[-1] if responses else None
    finally:
        page.remove_listener('response',response_received)

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.linkedin.com/authwall?trk=bf&trkInfo=AQFTxIvgmMYlTQAAAZ_2sKqow51b4a9hYVuIjhnQmRe8SKtovRGlAQyKoMkYJViFyb77z5at81EzhwNihO2sbpKtnQ9lrmjp21gawqnxJIzDAdHWPCFOLDnWbNtN6YB3VV1uR78=&original_referer=&sessionRedirect=https%3A%2F%2Fae.linkedin.com%2Fjobs%2Fview%2Fforward-deployed-ai-integrator-field-engineering-at-amazon-web-services-aws-4443029526', wait_until='networkidle')
    
    print('Testing other locators:')
    try:
        print('text="Sign in":', page.locator('text="Sign in"').first.is_visible())
    except Exception as e: print(e)
    try:
        print('text="تسجيل الدخول":', page.locator('text="تسجيل الدخول"').first.is_visible())
    except Exception as e: print(e)
    
    # Let's print out the tag names of elements containing 'Sign in'
    elements = page.locator('text="Sign in"').all()
    for el in elements:
        print(f'Tag: {el.evaluate("node => node.tagName")}, Visible: {el.is_visible()}')

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    stealth(page)
    page.goto('https://jooble.org/jdp/-4372668689877989571', wait_until='domcontentloaded')
    print('Apply links/buttons:')
    for el in page.query_selector_all('a, button, [role="button"]'):
        text = el.inner_text().strip() if el.inner_text() else ''
        if 'apply' in text.lower() or 'website' in text.lower() or 'company' in text.lower():
            tag = el.evaluate("el => el.tagName")
            href = el.get_attribute("href")
            print(f"- [{tag}] {text} (href: {href})")
    
    browser.close()

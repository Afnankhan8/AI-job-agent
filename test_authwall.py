from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://www.linkedin.com/authwall?trk=bf&trkInfo=AQFTxIvgmMYlTQAAAZ_2sKqow51b4a9hYVuIjhnQmRe8SKtovRGlAQyKoMkYJViFyb77z5at81EzhwNihO2sbpKtnQ9lrmjp21gawqnxJIzDAdHWPCFOLDnWbNtN6YB3VV1uR78=&original_referer=&sessionRedirect=https%3A%2F%2Fae.linkedin.com%2Fjobs%2Fview%2Fforward-deployed-ai-integrator-field-engineering-at-amazon-web-services-aws-4443029526', wait_until='networkidle')
    
    sign_in_link = page.locator("a:has-text('Sign in'), a:has-text('تسجيل الدخول'), a.main__sign-in-link").first
    print('Sign in visible?', sign_in_link.is_visible())
    
    if sign_in_link.is_visible():
        sign_in_link.click(force=True)
        page.wait_for_timeout(2000)
        print('Navigated to:', page.url)
        print('Inputs visible?', page.locator("input[name='session_key'], input[id='username'], input[type='email'], #email-or-phone").first.is_visible())
    else:
        print('Sign in not visible!')
        print(page.content())

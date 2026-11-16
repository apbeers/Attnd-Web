"""Browser end-to-end test of ticketing against dev (spec: Testing → Browser end-to-end). LEAN CI: runs only in the
"E2E · dev" job, which needs the repository variable CI_FULL=true.

    pip install playwright && playwright install --with-deps chromium
    E2E_SERVER=https://dev.attnd.app E2E_ORGANIZER_EMAIL=… E2E_ORGANIZER_PASSWORD=… python e2e/tickets_e2e.py

Flow: buyer opens TIXDEMO's ticket page → 1 × General Admission (all-in $26.06) → details → Stripe Checkout (test card
4242 4242 4242 4242) → back on the order page, "You're going!" with a QR code → the organizer (a non-staff test
account with a password, so no passkey step) signs in and sees the order in Tickets → Orders.
"""
import os
import re
import sys
import uuid

SERVER = os.getenv('E2E_SERVER', '').rstrip('/')
ORGANIZER = os.getenv('E2E_ORGANIZER_EMAIL', '')
PASSWORD = os.getenv('E2E_ORGANIZER_PASSWORD', '')
EVENT = os.getenv('E2E_EVENT_CODE', 'TIXDEMO')


def main():
    if not SERVER:
        print('E2E_SERVER is not set: skipped')
        return 0
    from playwright.sync_api import expect, sync_playwright
    email = f'e2e-{uuid.uuid4().hex[:8]}@ci.attnd.test'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f'{SERVER}/t/{EVENT}/')
        expect(page.get_by_text('$26.06').first).to_be_visible()
        page.get_by_label('Quantity').first.select_option('1')
        page.get_by_role('button', name='Get tickets').click()
        page.get_by_label('Email (your tickets go here)').fill(email)
        page.get_by_label('Name').first.fill('E2E Buyer')
        page.locator('input[name=terms]').check()
        page.get_by_role('button', name=re.compile(r'^Pay ')).click()
        page.wait_for_url(re.compile(r'https://checkout\.stripe\.com/.*'), timeout=30000)
        page.locator('#cardNumber').fill('4242 4242 4242 4242')
        page.locator('#cardExpiry').fill('12 / 34')
        page.locator('#cardCvc').fill('123')
        page.locator('#billingName').fill('E2E Buyer')
        if page.locator('#billingPostalCode').count():
            page.locator('#billingPostalCode').fill('76102')
        page.locator('button[type=submit]').click()
        page.wait_for_url(re.compile(re.escape(SERVER) + r'/tickets/order/.*'), timeout=60000)
        expect(page.get_by_text('You’re going!')).to_be_visible(timeout=60000)
        ref = page.locator('.tix-code').first.inner_text()
        if ORGANIZER and PASSWORD:
            console = browser.new_page()
            console.goto(f'{SERVER}/accounts/login/')
            console.locator('input[name=username]').fill(ORGANIZER)
            console.locator('input[name=password]').fill(PASSWORD)
            console.locator('form [type=submit]').first.click()
            console.goto(f'{SERVER}/console/')
            console.get_by_role('link', name=re.compile('Prairie Lights')).first.click()
            console.get_by_role('link', name=re.compile('Tickets')).first.click()
            console.get_by_role('link', name='Orders').click()
            expect(console.get_by_text(ref)).to_be_visible()
        browser.close()
    print(f'ok: order {ref} for {email}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

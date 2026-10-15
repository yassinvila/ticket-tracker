"""FISHER Oct 17 2026 ticket monitor. Browser-rendered public advertised prices.
No CAPTCHA solving, stealth proxying, login automation, or purchase automation.
"""
from __future__ import annotations
import csv, html, json, os, re, smtplib
import requests
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

NY = ZoneInfo('America/New_York')
STOP = datetime(2026, 10, 17, 17, 45, tzinfo=NY)
MODE = os.getenv('TRACKER_MODE', 'test').lower()
LIMIT = float(os.getenv('PRICE_LIMIT', '100'))
HISTORY = Path('data/price_history.csv')
STATE = Path('data/state.json')
# These URLs are for the correct event. CrowdVolt, Gametime and Friday's TicketData are excluded.
SOURCES = {
    'Vivid Seats': 'https://www.vividseats.com/fisher---dj-tickets-forest-hills-forest-hills-stadium-at-west-side-tennis-club-10-17-2026--concerts-dance-electronica/production/7151448',
    'SeatGeek': 'https://seatgeek.com/fisher-tickets/forest-hills-new-york-forest-hills-stadium-2026-10-17-5-45-pm/concert/18301903',
    'TickPick': 'https://www.tickpick.com/buy-fisher-artist-tickets-forest-hills-stadium-10-17-26-5pm/8045664/',
    'NightOut': 'https://nightout.com/events/v/fisher-dj-forest-hills-stadium-at-west-side-tennis-club-forest-hills-2026-10-17/tickets',
    'Ticketsware': 'https://ticketsware.com/concerts/fisher-artist/forest-hills-stadium-2026-10-17-1745',
}
# Other pages are retained as manual links until they have tested parsers.
MANUAL_LINKS = {
    'StubHub': 'https://www.stubhub.com/nz/fisher-flushing-tickets-10-17-2026/event/161348192/',
    'AXS': 'https://www.axs.com/events/1486750/fisher-tickets',
}
MONEY = r'\$\s*([\d,]+(?:\.\d{1,2})?)'
DATE = re.compile(r'(?:Oct(?:ober)?\s*17\b|10/17/2026|2026-10-17)', re.I)

def dollars(match):
    return round(float(match.group(1).replace(',', '')), 2)

def extract(site, text):
    """Return (price, explanation); never use unrelated dollar figures."""
    text = ' '.join(text.split())
    if not re.search(r'\bfisher\b', text, re.I) or not DATE.search(text) or 'forest hills' not in text.lower():
        return None, 'Event not identifiable in returned page'
    if site == "Vivid Seats":
        first_card = page.locator(
            '[data-testid="listing-group-row-container"]'
        ).first

        first_card.wait_for(state="visible", timeout=20000)

        price_text = first_card.locator(
            '[data-testid="listing-price"]'
        ).inner_text()

        match = re.search(r'\$([\d,]+(?:\.\d{2})?)', price_text)

        if not match:
            raise ValueError("Vivid Seats listing price missing")

        price = float(match.group(1).replace(",", ""))

        fees_included = first_card.locator(
            '[data-testid="fees-included-text"]'
        ).count() > 0

        detail = (
            "First listing, fees included"
            if fees_included
            else "First listing, fees not verified"
        )
    else:
        price, detail = extract(site, text)
    if site == 'SeatGeek':
        # The event page has individual GA listings. Avoid map prices and other
        # dollar amounts by extracting only within a section listing block.
        # Examples: "GA Areas 2 tickets $165 incl. fees" or
        # "GA Areas 1–6 tickets Amazing $173 incl. fees".
        prices=[]
        for section in re.split(r'(?=\b(?:GA Areas|VIP Pit|CLUB \d{3})\b)', text, flags=re.I)[1:]:
            # Cap the section before a map/next unrelated element.
            if not re.match(r'GA Areas\b', section, re.I):
                continue
            match=re.search(r'\$\s*([\d,]+(?:\.\d{1,2})?)\s*(?:incl\.?\s*fees|including fees)', section[:240], re.I)
            if match:
                prices.append(dollars(match))
        if prices:
            return min(prices), 'Cheapest visible GA listing, labeled fees included; checkout not verified'
        return None, 'GA listing prices not available in rendered page'
    if site == 'TickPick':
        patterns = [r'Prices for Fisher.{0,320}?start at\s*'+MONEY,
                    r'General Admission.{0,140}?'+MONEY+r'\s*(?:ea|each)']
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m: return dollars(m), 'Event-specific advertised/listed price; checkout not verified'
        return None, 'Price not found in rendered page'
    if site == 'NightOut':
        for pat in [r'General Admission starts at\s*'+MONEY,
                    r'Cheapest seats right now.{0,100}?'+MONEY+r'\s*all-in',
                    r'Tickets from\s*'+MONEY+r'\s*all-in']:
            m = re.search(pat, text, re.I)
            if m: return dollars(m), 'Advertised all-in starting price; checkout not verified'
        return None, 'No labeled current price (historical values ignored)'
    if site == 'Ticketsware':
        m = re.search(r'(?:tickets\s*)?from\s*'+MONEY+r'\s*(?:1\s*seller|seller)', text, re.I)
        return (dollars(m), 'Event advertised floor; other-event prices ignored') if m else (None, 'Labeled event floor missing')
    return None, 'No supported event-specific parser'

def check(browser, site, url, now):
    record = {'time':now, 'site':site, 'price':'', 'status':'', 'detail':'', 'url':url}
    page = browser.new_page()
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
        status = response.status if response else None
        if status in (403, 429):
            record.update(status=f'HTTP {status}', detail='Access restricted; no retry/bypass attempted')
            return record
        if status and status >= 400:
            record.update(status=f'HTTP {status}', detail='Website returned an error')
            return record
        # Wait briefly for client-rendered text, without clicking or handling access controls.
        try: page.locator('body').wait_for(state='visible', timeout=7000)
        except PlaywrightTimeout: pass
        page.wait_for_timeout(1700)
        text = page.locator('body').inner_text(timeout=10000)
        if re.search(r'(verify you are human|access denied|captcha|unusual traffic)', text, re.I):
            record.update(status='Access challenge',detail='Manual check needed')
            return record
        price, detail = extract(site, text)
        record.update(price=price if price is not None else '', status='Price found' if price is not None else 'No verified extraction', detail=detail)
    except Exception as exc:
        record.update(status='Browser error', detail=f'{type(exc).__name__}: {str(exc)[:130]}')
    finally:
        page.close()
    return record

def check_ticketmaster(now):
    """Official Discovery API: event-level price range, not a live resale/cart quote."""
    site = 'Ticketmaster'
    base = 'https://app.ticketmaster.com/discovery/v2/events.json'
    browse_url = 'https://www.ticketmaster.com/fisher-tickets/artist/847492'
    record = {'time': now, 'site': site, 'price': '', 'status': '',
              'detail': '', 'url': browse_url}
    key = os.getenv('TICKETMASTER_API_KEY', '').strip()
    if not key:
        record.update(status='API key missing', detail='Set TICKETMASTER_API_KEY as a GitHub Actions secret')
        return record
    try:
        resp = requests.get(base, params={
            'apikey': key, 'keyword': 'Fisher', 'startDateTime': '2026-10-17T00:00:00Z',
            'endDateTime': '2026-10-18T04:00:00Z', 'countryCode': 'US',
            'size': 100,
        }, timeout=20)
        if resp.status_code in (401, 403, 429):
            record.update(status=f'API HTTP {resp.status_code}', detail='Ticketmaster API rejected request or rate-limited')
            return record
        resp.raise_for_status()
        events = resp.json().get('_embedded', {}).get('events', [])
        matches = []
        for e in events:
            name = e.get('name', '')
            date = e.get('dates', {}).get('start', {}).get('localDate', '')
            venues = e.get('_embedded', {}).get('venues', [])
            if not (re.search(r'\bfisher\b', name, re.I) and date == '2026-10-17'
                    and any('forest hills stadium' in v.get('name', '').lower() for v in venues)):
                continue
            matches.append(e)
        if not matches:
            record.update(status='Not found in API', detail='No matching Oct 17 Forest Hills Stadium event in Discovery API response')
            return record
        event = matches[0]
        record['url'] = event.get('url') or browse_url
        ranges = event.get('priceRanges') or []
        usd = [v for v in ranges if v.get('currency') == 'USD' and isinstance(v.get('min'), (float, int))]
        if not usd:
            record.update(status='No API price', detail='Event confirmed in API, but priceRanges.min is unavailable')
            return record
        price = min(float(v['min']) for v in usd)
        record.update(price=round(price, 2), status='API price found',
                      detail='Ticketmaster published minimum; fees and availability unverified')
    except (requests.RequestException, ValueError, KeyError) as exc:
        record.update(status='API error', detail=f'{type(exc).__name__}: {str(exc)[:150]}')
    return record

def get_state():
    try: return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}

def save_state(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')

def add_history(records):
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    fresh = not HISTORY.exists()
    with HISTORY.open('a',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['time','site','price','status','detail','url'])
        if fresh: writer.writeheader()
        writer.writerows(records)

def report(records):
    rows=[]
    for r in records:
        value = f"${float(r['price']):,.2f}" if r['price'] != '' else r['status']
        rows.append(f'<tr><td style="padding:7px;border-bottom:1px solid #ddd"><a href="{html.escape(r["url"],quote=True)}">{html.escape(r["site"])}</a></td><td style="padding:7px;border-bottom:1px solid #ddd">{html.escape(value)}</td></tr>')
    table=''.join(rows)
    return f'<html><body style="font:14px Arial"><b>FISHER — Sat Oct 17</b><p>Test: advertised single-ticket prices; checkout not confirmed.</p><table><tr><th>Site</th><th>Price/status</th></tr>{table}</table></body></html>'

def send_email(subject, text, html_body):
    sender=os.getenv('SMTP_EMAIL','')
    password=os.getenv('SMTP_APP_PASSWORD','')
    if not (sender and password):
        raise RuntimeError('Set SMTP_EMAIL and SMTP_APP_PASSWORD in GitHub Actions secrets')
    recipient=os.getenv('ALERT_TO','yassinvila.info@gmail.com')
    mail=EmailMessage()
    mail['From']=sender; mail['To']=recipient; mail['Subject']=subject
    mail.set_content(text); mail.add_alternative(html_body,subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(sender,password.replace(' ','')); server.send_message(mail)

def dispatch(records):
    state=get_state()
    if MODE not in ('test','threshold'): raise ValueError('TRACKER_MODE must be test or threshold')
    if MODE=='test':
        send_email('[TEST] FISHER Oct 17 — prices', '\n'.join(f"{r['site']}: {r['price'] or r['status']}" for r in records), report(records))
        return 'Test digest sent'
    old=state.setdefault('best_alerted',{})
    count=0
    for r in records:
        if r['price']=='': continue
        p=float(r['price'])
        if p>=LIMIT or (r['site'] in old and p>=float(old[r['site']])): continue
        send_email(f'FISHER: {r["site"]} from ${p:.2f}', f'Advertised one-ticket floor: ${p:.2f}. Checkout unverified.\n{r["url"]}', report([r]))
        old[r['site']]=p; save_state(state); count+=1
    return f'{count} alerts sent'

def main():
    if datetime.now(NY)>=STOP:
        print('Event start passed; stopped.');return
    now=datetime.now(NY).isoformat(timespec='seconds')
    results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=os.getenv('HEADED','0')!='1')
        try:
            for site,url in SOURCES.items():
                r=check(browser,site,url,now)
                results.append(r)
                print(f"{site}: {r['status']} {r['price']} {r['detail']}")
        finally: browser.close()
    tm = check_ticketmaster(now)
    results.append(tm)
    print(f"Ticketmaster: {tm['status']} {tm['price']} {tm['detail']}")
    for site,url in MANUAL_LINKS.items():
        results.append({'time':now,'site':site,'price':'','status':'Manual check','detail':'Automated access not enabled','url':url})
    add_history(results)
    print(dispatch(results))

if __name__=='__main__':main()

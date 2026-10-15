"""FISHER 2026-10-17: observable public prices; never infer checkout prices or inventory."""
from __future__ import annotations
import csv, html, json, os, re, smtplib, sys, time
from dataclasses import dataclass, asdict
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

NY = ZoneInfo('America/New_York')
END = datetime(2026, 10, 17, 17, 45, tzinfo=NY)
MODE = os.getenv('TRACKER_MODE', 'test').strip().lower()  # test / threshold
TARGET = float(os.getenv('PRICE_LIMIT', '100'))
MIN_TICKETS = int(os.getenv('MIN_TICKETS', '3'))
REPORT_EVERY_RUN = os.getenv('REPORT_EVERY_RUN', 'true').lower() == 'true'
HISTORY = Path('data/price_history.csv')
STATE = Path('data/state.json')
# Exact event pages preferred; artist-level listings require contextual date checks.
SOURCES = {
 'CrowdVolt': 'https://www.crowdvolt.com/event/fisher-sat-oct-17-forest-hill-new-york',
 'Vivid Seats': 'https://www.vividseats.com/en/fisher---dj-tickets-forest-hills-forest-hills-stadium-at-west-side-tennis-club-10-17-2026--concerts-dance-electronica/production/7151448',
 'SeatGeek': 'https://seatgeek.com/fisher-tickets/nyc',
 'TickPick': 'https://www.tickpick.com/buy-fisher-artist-tickets-forest-hills-stadium-10-17-26-5pm/8045664/',
 'StubHub': 'https://www.stubhub.com/nz/fisher-flushing-tickets-10-17-2026/event/161348192/',
 'AXS': 'https://www.axs.com/events/1486750/fisher-tickets',
 'NightOut': 'https://nightout.com/events/v/fisher-dj-forest-hills-stadium-at-west-side-tennis-club-forest-hills-2026-10-17/tickets',
 'The King of Tickets': 'https://thekingoftickets.com/performer/fisher-tickets',
 'TicketData': 'https://www.ticketdata.com/performer/fisher-dj',
 'Ticketsware': 'https://ticketsware.com/concerts/fisher-artist/forest-hills-stadium-2026-10-17-1745',
}
EVENT_URL = {'CrowdVolt','Vivid Seats','TickPick','StubHub','AXS','NightOut','Ticketsware'}
HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; PersonalEventPriceMonitor/2.0)', 'Accept-Language':'en-US,en;q=0.9'}
DOLLAR = re.compile(r'(?<!\w)\$\s*([0-9]{1,4}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)')
DATE = re.compile(r'(?:Oct(?:ober)?\s*17(?:,?\s*2026)?|10/17/2026|2026-10-17|Sat(?:urday)?\s*,?\s*Oct(?:ober)?\s*17)',re.I)

@dataclass
class Observation:
 timestamp: str
 site: str
 url: str
 status: str
 prices_usd: str = '[]'             # JSON array of visible monetary mentions, not listings
 lowest_visible_usd: str = ''
 quantity_hint: str = ''
 all_in_verified: bool = False
 quantity_verified: bool = False
 listing_id: str = ''
 note: str = ''


def parse_html(site: str, url: str, source_html: str, timestamp: str) -> Observation:
    soup = BeautifulSoup(source_html,'html.parser')
    for el in soup(['script','style','noscript','svg','footer','nav']):el.decompose()
    text = ' '.join(soup.get_text(' ', strip=True).split())
    # Event-specific pages must mention FISHER and either date or venue; broad pages need the date.
    if not re.search(r'\bFISHER\b',text,re.I):
        return Observation(timestamp,site,url,'event_unconfirmed',note='No FISHER identification in visible HTML')
    if site not in EVENT_URL and not DATE.search(text):
        return Observation(timestamp,site,url,'event_unconfirmed',note='October 17 not found')
    if site in EVENT_URL and not (DATE.search(text) or 'Forest Hills' in text):
        return Observation(timestamp,site,url,'event_unconfirmed',note='Venue/date not found')
    # Narrow to event context on artist pages; avoid accidentally including October 16 or other shows.
    if site not in EVENT_URL:
        snippets=[]
        for m in DATE.finditer(text):
            snippets.append(text[max(0,m.start()-100):min(len(text),m.end()+260)])
        text=' | '.join(snippets)
    else:
        text=text[:18000]
    nums=[]
    for m in DOLLAR.finditer(text):
        p=float(m.group(1).replace(',',''))
        if 1 <= p <= 10000 and p not in nums:nums.append(p)
        if len(nums)>=60:break
    qty=[]
    for match in re.finditer(r'\b(?:1\s*[-–]\s*3|3\s*[-–]\s*\d+|3|4|5|6)\s*(?:tickets?|tix)\b',text,re.I):
        q=match.group(0)
        if q not in qty:qty.append(q)
        if len(qty)>=5:break
    return Observation(timestamp,site,url,'visible_prices_unverified' if nums else 'no_price_in_html',
        json.dumps(nums), str(min(nums)) if nums else '', ', '.join(qty),
        note='Visible dollar amounts only; not all necessarily current listing prices. Quantity, fee-inclusive checkout price and listing identity are unverified.')


def fetch(site: str,url: str,timestamp: str, session=None) -> Observation:
    try:
        r=(session or requests).get(url,headers=HEADERS,timeout=17)
        r.raise_for_status()
        return parse_html(site,url,r.text,timestamp)
    except requests.RequestException as e:
        return Observation(timestamp,site,url,'request_failed',note=f'{type(e).__name__}: {str(e)[:150]}')


def qualified(o: Observation) -> bool:
    # Only usable once a future connector actually verifies the individual offer.
    if not(o.all_in_verified and o.quantity_verified and o.listing_id):return False
    try: return float(o.lowest_visible_usd) < TARGET
    except ValueError:return False


def load_state():
    try:return json.loads(STATE.read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError):return {'last_digest':'','sent_listings':{}}


def save_state(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')


def append_history(items):
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    new=not HISTORY.exists()
    with HISTORY.open('a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(Observation.__dataclass_fields__))
        if new:w.writeheader()
        for item in items:w.writerow(asdict(item))


def make_report(items):
    lines=['FISHER — October 17, 2026 — Forest Hills Stadium',
           f'Mode: {MODE.upper()} | Limit: strictly below ${TARGET:g} all-in | Quantity: {MIN_TICKETS}+ together',
           f'Checked: {items[0].timestamp if items else "n/a"}', '',
           'TEST REPORT: prices are visible dollar amounts, not verified cart totals.']
    for o in items:
        prices=', '.join('$'+f'{float(x):.2f}' for x in json.loads(o.prices_usd)) or 'none extracted'
        lines += ['',f'{o.site} — {o.status}',f'Visible amounts: {prices}',
                  f'Quantity text: {o.quantity_hint or "not visible"}',
                  'Checkout all-in verified: '+('YES' if o.all_in_verified else 'NO'),
                  f'Link: {o.url}',f'Note: {o.note}']
    return '\n'.join(lines)+'\n'


def send_email(subject,body):
    recipient=os.getenv('ALERT_TO','yassinvila.info@gmail.com')
    # Free Gmail SMTP (app password, NOT account password), or Resend API if verified domain configured.
    resend_key=os.getenv('RESEND_API_KEY','')
    if resend_key:
        sender=os.getenv('RESEND_FROM','')
        if not sender:raise RuntimeError('RESEND_FROM required for Resend; verified sender/domain may be required')
        res=requests.post('https://api.resend.com/emails',headers={'Authorization':'Bearer '+resend_key,'Content-Type':'application/json'},json={'from':sender,'to':[recipient],'subject':subject,'text':body},timeout=20)
        res.raise_for_status()
        return
    sender=os.getenv('SMTP_EMAIL','')
    password=os.getenv('SMTP_APP_PASSWORD','')
    if not sender or not password:raise RuntimeError('Configure RESEND_API_KEY+RESEND_FROM or SMTP_EMAIL+SMTP_APP_PASSWORD. Nothing was sent.')
    msg=EmailMessage();msg['From']=sender;msg['To']=recipient;msg['Subject']=subject;msg.set_content(body)
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(sender,password.replace(' ',''));server.send_message(msg)


def dispatch(items,state):
    if MODE=='test':
        report=make_report(items)
        # Keep one digest per execution, not one mail per site. Enabled by default for testing.
        if REPORT_EVERY_RUN or state.get('last_digest')!=report:
            send_email('[TEST] FISHER Oct 17 — resale website price report',report)
            state['last_digest']=report
            save_state(state)
            return 'test report emailed'
        return 'same report skipped'
    if MODE!='threshold':raise ValueError('TRACKER_MODE must be test or threshold')
    sent=state.setdefault('sent_listings',{})
    count=0
    for o in items:
        if not qualified(o):continue
        key=o.site+':'+o.listing_id
        price=float(o.lowest_visible_usd)
        if key in sent and price>=float(sent[key]):continue
        send_email(f'FISHER: {o.site} ${price:.2f} per ticket',
                   f'{MIN_TICKETS}+ together and fee-inclusive confirmed.\nListing: {o.url}\nSeen: {o.timestamp}')
        sent[key]=price;save_state(state);count+=1
    return f'{count} qualifying alerts sent (unverified prices never alert)'


def main():
    if datetime.now(NY)>=END:
        print('Event started; stopping.');return
    stamp=datetime.now(NY).isoformat(timespec='seconds')
    items=[fetch(site,url,stamp) for site,url in SOURCES.items()]
    append_history(items)
    for item in items:print(f'{item.site}: {item.status} | {item.lowest_visible_usd or "N/A"}')
    print(dispatch(items,load_state()))

if __name__=='__main__':main()

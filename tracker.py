"""FISHER 2026-10-17: observable public prices; never infer checkout prices or inventory."""
from __future__ import annotations
import csv, html, json, os, re, smtplib, sys, time, random
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
# Sites blocking requests are checked again only after a cooldown, not every 5 minutes.
BLOCK_COOLDOWN_HOURS = int(os.getenv('BLOCK_COOLDOWN_HOURS','12'))
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


# Only explicitly labeled, event-specific advertised prices. Never scan the entire page
# for dollar signs: historical statistics and other concert listings are not offers.
PRICE_PATTERNS = {
 'CrowdVolt': r'Tickets from\s*\$([\d,]+(?:\.\d{1,2})?)',
 'NightOut': r'general admission starts at\s*\$([\d,]+(?:\.\d{1,2})?)\s+all-in',
 'Ticketsware': r'from\s*\$([\d,]+(?:\.\d{1,2})?)\s+1 seller',
 'Vivid Seats': r'tickets start at\s*\$([\d,]+(?:\.\d{1,2})?)',
 'SeatGeek': r'Oct\s*17.{0,120}?(?:\$|From \$)([\d,]+(?:\.\d{1,2})?)',
}

def parse_html(site: str, url: str, source_html: str, timestamp: str) -> Observation:
    soup=BeautifulSoup(source_html,'html.parser')
    # Remove scripts and unrelated invisible text; leave a normalized prose snapshot.
    for el in soup(['script','style','noscript','svg','footer','nav']): el.decompose()
    text=' '.join(soup.get_text(' ',strip=True).split())
    if not re.search(r'\bFISHER\b',text,re.I):
        return Observation(timestamp,site,url,'event_unconfirmed',note='Artist not confirmed in HTML')
    if site not in EVENT_URL and not DATE.search(text):
        return Observation(timestamp,site,url,'event_unconfirmed',note='October 17 not present')
    if site in EVENT_URL and not (DATE.search(text) and 'Forest Hills' in text):
        return Observation(timestamp,site,url,'event_unconfirmed',note='Exact event not confirmed')
    pattern=PRICE_PATTERNS.get(site)
    match=re.search(pattern,text,re.I) if pattern else None
    if not match:
        return Observation(timestamp,site,url,'no_price_in_html',note='No reliably labeled event price accessible')
    price=float(match.group(1).replace(',',''))
    if not 1<=price<=10000:
        return Observation(timestamp,site,url,'no_price_in_html',note='Invalid advertised price')
    qty=''
    if site=='CrowdVolt':
        quantities=re.findall(r'General Admission\s+1[-–](\d+)\s+Tickets',text,re.I)
        if quantities: qty='Advertised ranges up to '+str(max(map(int,quantities)))+'; NOT linked to the floor price'
    is_all_in=site in ('NightOut','Vivid Seats')
    freshness=''; stamp=re.search(r'Prices refreshed on\s*([\d-]+\s+[\d:]+\s*UTC)',text,re.I)
    if stamp: freshness=' Published refresh: '+stamp.group(1)+'.'
    return Observation(timestamp,site,url,'advertised_from_price',json.dumps([price]),str(price),qty,
        False,False,'',('Public advertised starting price'+(' (site labels as all-in)' if is_all_in else ' (fees unverified)')+
        '; quantity 3 together NOT verified.'+freshness))


def fetch(site: str,url: str,timestamp: str, session=None) -> Observation:
    try:

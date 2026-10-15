    with HISTORY.open('a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(Observation.__dataclass_fields__))
        if new:w.writeheader()
        for item in items:w.writerow(asdict(item))


def brief_status(o):
    if o.status=='advertised_from_price':
        label='From $'+format(float(o.lowest_visible_usd),',.2f')
        if o.site in ('NightOut','Vivid Seats'):label+=' (published all-in)'
        return label+' [3 together unverified]'
    if o.status=='visible_prices_unverified':
        try:
            nums=sorted(set(float(n) for n in json.loads(o.prices_usd)))
        except (ValueError,TypeError): nums=[]
        # Don't confuse arbitrary dollar amounts with tickets or call the minimum a ticket price.
        return 'Visible $: '+(', '.join(f'${n:,.2f}' for n in nums[:5]) +
                            (f' (+{len(nums)-5} more)' if len(nums)>5 else '') if nums else 'none')+' [unverified]'
    return {'blocked':'Blocked (403)','rate_limited':'Rate-limited (429)',
            'cooldown':'Skipped (temporary cooldown)','event_unconfirmed':'Event unconfirmed',
            'no_price_in_html':'No reliable event price','request_failed':'Connection failed',
            'http_error':'HTTP error'}.get(o.status,o.status)


def make_report(items):
    stamp=items[0].timestamp if items else 'n/a'
    lines=[f'FISHER · Oct 17 | {stamp}', 'TEST — event-specific advertised FROM prices; 3 tickets together NOT verified.',
           'Source | Results | URL']
    for o in items:
        lines.append(f'{o.site} | {brief_status(o)} | {o.url}')
    return '\n'.join(lines)+'\n'


def make_html_report(items):
    stamp=html.escape(items[0].timestamp if items else 'n/a')
    rows=[]
    for o in items:
        status=html.escape(brief_status(o))
        site=html.escape(o.site)
        url=html.escape(o.url, quote=True)
        rows.append(f'<tr><td style="padding:7px;border-bottom:1px solid #ddd"><a href="{url}">{site}</a></td>'
                    f'<td style="padding:7px;border-bottom:1px solid #ddd">{status}</td></tr>')
    return (f'<html><body style="font:14px Arial,sans-serif;max-width:680px">'
            f'<h3>FISHER · Oct 17 · Forest Hills</h3>'
            f'<p>Checked {stamp}<br><b>TEST MODE:</b> advertised event FROM prices only; 3 together unverified.</p>'
            f'<table style="border-collapse:collapse;width:100%"><tr><th align="left">Website</th>'
            f'<th align="left">Visible amounts / status</th></tr>{"".join(rows)}</table></body></html>')


def send_email(subject,body,html_body=None):
    recipient=os.getenv('ALERT_TO','yassinvila.info@gmail.com')
    # Free Gmail SMTP (app password, NOT account password), or Resend API if verified domain configured.
    resend_key=os.getenv('RESEND_API_KEY','')
    if resend_key:
        sender=os.getenv('RESEND_FROM','')
        if not sender:raise RuntimeError('RESEND_FROM required for Resend; verified sender/domain may be required')
        res=requests.post('https://api.resend.com/emails',headers={'Authorization':'Bearer '+resend_key,'Content-Type':'application/json'},json={'from':sender,'to':[recipient],'subject':subject,'text':body,**({'html':html_body} if html_body else {})},timeout=20)
        res.raise_for_status()
        return
    sender=os.getenv('SMTP_EMAIL','')
    password=os.getenv('SMTP_APP_PASSWORD','')
    if not sender or not password:raise RuntimeError('Configure RESEND_API_KEY+RESEND_FROM or SMTP_EMAIL+SMTP_APP_PASSWORD. Nothing was sent.')
    msg=EmailMessage();msg['From']=sender;msg['To']=recipient;msg['Subject']=subject;msg.set_content(body)
    if html_body:msg.add_alternative(html_body,subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(sender,password.replace(' ',''));server.send_message(msg)


def dispatch(items,state):
    if MODE=='test':
        report=make_report(items)
        # Keep one digest per execution, not one mail per site. Enabled by default for testing.
        if REPORT_EVERY_RUN or state.get('last_digest')!=report:
            send_email('[TEST] FISHER Oct 17 — price snapshot',report,make_html_report(items))
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
    state=load_state()
    blocked=state.setdefault('blocked_until',{})
    items=[]
    for site,url in SOURCES.items():
        if time.time() < float(blocked.get(site,0)):
            o=Observation(stamp,site,url,'cooldown',note='Previous HTTP 403/429; retry after cooldown')
        else:
            # Small polite spacing reduces bursts; not an anti-bot bypass.
            if items: time.sleep(random.uniform(1.5,3.5))
            o=fetch(site,url,stamp)
            if o.status in ('blocked','rate_limited'):
                blocked[site]=time.time()+BLOCK_COOLDOWN_HOURS*3600
        items.append(o)
    save_state(state)
    append_history(items)
    for item in items:print(f'{item.site}: {item.status} | {item.lowest_visible_usd or "N/A"}')
    print(dispatch(items,state))

if __name__=='__main__':main()
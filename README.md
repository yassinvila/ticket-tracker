# FISHER Oct 17 — Free Public Price Monitor (v2)

**Status:** Runnable Python and GitHub Actions workflow, but live website extraction is **not verified**. These sites may block server-side requests, alter their HTML or load inventory in JavaScript. Prices can be stale, unrelated to listings, or incomplete. This is not a fully working quantity-and-checkout-verified ticket scraper.

## Included sources
CrowdVolt, Vivid Seats, SeatGeek, TickPick, StubHub, AXS, NightOut, The King of Tickets, TicketData, Ticketsware. Source URLs in `tracker.py` can be edited. The application collects public visible US-dollar mentions, not authenticated inventory. An event confirmation gate reduces accidental other-date matches; it is **not perfect**.

## Test mode (default)
`TRACKER_MODE=test` sends **one consolidated report per run** to `yassinvila.info@gmail.com`. The report includes **all unique dollar amounts extracted from the visible page text (up to 60 per source)**, source link, observed quantity phrases, and extraction status. Dollar amounts may include unrelated prices and are never represented as checkout-verified listings. Failure or zero results are included too. `REPORT_EVERY_RUN=true` means **up to 288 messages/day** while the workflow runs. Set false to attempt deduplicating exact reports, but timestamps currently make each report different. GitHub Actions schedule is best-effort, not guaranteed every five minutes.

## Threshold mode (when a real listing-level connector exists)
Set `TRACKER_MODE=threshold`, `PRICE_LIMIT=100`, `MIN_TICKETS=3`. It emails for **new verified listing IDs or verified price drops strictly below $100 per ticket**, provided quantity and all-in price are confirmed. **None of the included public HTML connectors can verify all these fields yet**, so threshold mode deliberately sends **no buying alerts**. Do not switch and assume it works without adding a verified connector.

## Free cloud setup
1. Create a **public GitHub repository** and upload **contents** of this folder, including `.github/workflows/monitor.yml`. GitHub hosted public-repo standard runners are generally free; scheduled workflows can be delayed, disabled for inactivity or subject to limits.
2. In **Settings → Secrets and variables → Actions**, configure email **one of two ways**:
   - **Gmail SMTP:** secrets `SMTP_EMAIL` (sender Gmail account) and `SMTP_APP_PASSWORD` (Google app password, **not Gmail sign-in password**). Gmail usually requires 2-Step Verification for app passwords. The recipient is already coded; it is not the sender credentials.
   - **Resend:** secret `RESEND_API_KEY`, variable `RESEND_FROM` (verified sending identity). Resend's free account restrictions/verified domain requirements apply; check them before choosing.
3. In the same GitHub **Variables**, add `TRACKER_MODE=test`, `REPORT_EVERY_RUN=true`. Optionally add `PRICE_LIMIT=100`, `MIN_TICKETS=3`.
4. Open **Actions → FISHER Price Tracker → Run workflow**, inspect log and email. **No email means setup failed**; check Actions logs. You can also locally run `python -m unittest discover -s tests`.
5. Every execution appends observations to `data/price_history.csv` and records sent email state in `data/state.json` via GitHub commit. Public repo means **history, links, and configuration are public** (no credentials in committed files). Email credentials belong only in GitHub Secrets.
6. Automatically stops at **5:45 PM America/New_York, October 17, 2026**.

## Important constraints
- The execution environment used for development had no outbound DNS access, so live HTML scraping and real email delivery **could not be end-to-end verified**. Unit tests cover HTML parsing and alert safety.
- Web-search discovery confirmed event listings but **does not validate unattended HTML extraction**.
- No attempt is made to bypass CAPTCHAs, logins, or rate limits. Adjust polling if sites block or prohibit automated access.
- All-in means final mandatory fees included; promotional "all-in" claims alone do not establish exact three-ticket cart totals.
- At most 60 unique dollar figures per source is a safeguard against huge irrelevant pages; the report is therefore **all extracted values**, not every ticket for sale.

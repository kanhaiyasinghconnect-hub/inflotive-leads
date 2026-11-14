# INFLOTIVE Lead System

The whole system runs from a free CRM on your own computer. Each morning, one command does all of this:

1. **Checks your Gmail.** Anyone who replied is taken out of the email sequence and flagged **WhatsApp now**. "No thanks" replies are never contacted again, and bounced addresses are closed.
2. **Finds new leads.** Claude searches the web and reads each company's website. It covers newly opened real estate firms in Noida every day, plus other industries and cities across India in rotation. It qualifies each lead, picks only the relevant services, and drafts an email, 2 follow-ups, a LinkedIn note, an Instagram DM and a WhatsApp message.
3. **Checks contacts and duplicates.** Emails and phone numbers are checked against the company's website, and duplicates are skipped.
4. **Emails automatically.** High and Medium leads whose email was confirmed on their own website get the first email, then follow-up 1 on day 4 and follow-up 2 on day 9, all in one thread. Sending stops the moment they reply. There's a cap of 25 emails a day from your Gmail, sent 45–120 seconds apart, and every email has an opt-out line.
5. **Writes the daily report:** leads found, priorities, cities and industries, emails and follow-ups sent, replies, opt-outs, and who to WhatsApp.

Low-priority leads and leads with an unconfirmed email wait for you to click **Approve email** in the CRM.

**WhatsApp, LinkedIn and Instagram are not sent automatically.** Those platforms ban accounts that message strangers automatically. Leads who reply show up under **WhatsApp now** on the dashboard with a one-click button that opens WhatsApp with the message ready. Log it afterwards.

## Setup (once)

```bash
pip install -r requirements.txt
copy .env.example .env
```

Then open `.env` in Notepad and fill in:
- `ANTHROPIC_API_KEY`: from console.anthropic.com. It powers the research and costs roughly a few dollars a day.
- `SMTP_USER`: your Gmail address.
- `SMTP_PASSWORD`: a Gmail **App Password**. In your Google Account go to Security, turn on 2-Step Verification, then open App passwords. The same password is used to read replies.
- `SENDER_PHONE`, `SENDER_WEBSITE`: your signature details.

## Daily use

```bash
python run_daily.py              # the whole daily routine
python run_daily.py --dry-run    # everything except sending, and shows who would be emailed
python app.py                    # CRM at http://127.0.0.1:5055
```

### Run it automatically every morning at 10:00

```bash
schtasks /Create /SC DAILY /ST 10:00 /TN "INFLOTIVE daily leads" /TR "cmd /c cd /d \"C:\CLAUDE PLUGIN\inflotive-leads\" && python run_daily.py >> reports\run.log 2>&1"
```

The computer must be on at that time.

To switch back to approving every email yourself, set `AUTO_SEND=false` in `.env`.

## Files

| File | Purpose |
|---|---|
| `inflotive_brief.md` | Business brief Claude follows: positioning, rules, tone |
| `config.py` | Industries, cities, volumes, automatic-mode settings |
| `research.py` | AI research and drafting |
| `verify.py` | Checks emails and phone numbers against the company website |
| `outreach.py` | Automatic email sequence |
| `inbox.py` | Gmail reply, opt-out and bounce detection |
| `db.py` | CRM database and duplicate detection |
| `report.py` | Daily report |
| `app.py` + `templates/` | CRM web app |

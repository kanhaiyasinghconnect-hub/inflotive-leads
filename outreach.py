"""Email outreach: first email + 2 follow-ups in the same thread, stopping on any reply.

Automatic mode (config.AUTO_SEND): leads with priority in AUTO_SEND_PRIORITIES and an email
address confirmed on their own website are approved automatically. Other leads still need
"Approve email" in the CRM. Opted-out / bounced / replied leads are never emailed again.

LinkedIn, Instagram and cold WhatsApp are never automated (platform bans); WhatsApp is
flagged for leads who reply, so you can message people who have engaged.
"""
import random
import smtplib
import ssl
import time
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import config
import db

OPT_OUT = "If this isn't relevant, just reply \"no thanks\" and we won't contact you again."


def _signature() -> str:
    lines = [config.SENDER_NAME, config.SENDER_TITLE, config.SENDER_PHONE,
             config.SENDER_WEBSITE, config.SENDER_ADDRESS]
    return "\n".join(x for x in lines if x)


def _with_footer(body: str) -> str:
    body = (body or "").rstrip()
    if config.SENDER_NAME.lower() in body[-200:].lower():
        extra = [x for x in (config.SENDER_PHONE, config.SENDER_WEBSITE, config.SENDER_ADDRESS) if x]
        body += "\n" + "\n".join(extra)
    else:
        body += "\n\n" + _signature()
    return body + "\n\n" + OPT_OUT


def _subject(lead) -> str:
    return lead["email_subject"] or f"Quick idea for {lead['company_name']}"


def build_message(lead, step: int = 0) -> EmailMessage:
    """step 0 = first email, 1 = follow-up 1, 2 = follow-up 2 (threaded replies)."""
    msg = EmailMessage()
    msg["From"] = formataddr((config.SENDER_NAME, config.SMTP_USER))
    msg["To"] = lead["email"]
    domain = config.SMTP_USER.split("@")[-1] if "@" in config.SMTP_USER else "inflotive.local"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["List-Unsubscribe"] = f"<mailto:{config.SMTP_USER}?subject=unsubscribe>"
    if step == 0:
        msg["Subject"] = _subject(lead)
        body = lead["email_body"]
    else:
        msg["Subject"] = "Re: " + _subject(lead)
        if lead["last_message_id"]:
            msg["In-Reply-To"] = lead["last_message_id"]
            msg["References"] = lead["last_message_id"]
        body = lead["follow_up_1"] if step == 1 else lead["follow_up_2"]
    msg.set_content(_with_footer(body))
    return msg


# ---------------- queues ----------------
def sent_today(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM activities WHERE kind IN ('email_sent','followup_sent') AND ts LIKE ?",
        (date.today().isoformat() + "%",)).fetchone()[0]


def auto_approve(conn) -> int:
    """Approve qualifying new leads automatically (owner-authorised automatic mode)."""
    if not config.AUTO_SEND:
        return 0
    marks = ",".join("?" for _ in config.AUTO_SEND_PRIORITIES)
    rows = conn.execute(
        f"""SELECT id FROM leads WHERE outreach_status = 'New' AND email_verified = 1
            AND do_not_contact = 0 AND COALESCE(email_step, 0) = 0
            AND email IS NOT NULL AND email != '' AND priority IN ({marks})""",
        config.AUTO_SEND_PRIORITIES).fetchall()
    for r in rows:
        db.update_lead(conn, r["id"], {"email_approved": 1, "outreach_status": "Approved"})
        db.log_activity(conn, r["id"], "status", "Auto-approved for email (automatic mode)")
    conn.commit()
    return len(rows)


def approved_queue(conn):
    return conn.execute(
        """SELECT * FROM leads WHERE email_approved = 1 AND do_not_contact = 0
           AND email IS NOT NULL AND email != '' AND outreach_status = 'Approved'
           AND COALESCE(email_step, 0) = 0
           ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, id""").fetchall()


def followup_queue(conn):
    """Leads in an active sequence whose next follow-up date has arrived."""
    out = []
    rows = conn.execute(
        """SELECT * FROM leads WHERE email_step IN (1, 2) AND do_not_contact = 0
           AND outreach_status IN ('Contacted', 'Follow-up due')
           AND response_status = 'No response' AND first_email_date IS NOT NULL""").fetchall()
    today = date.today()
    for r in rows:
        step = r["email_step"]  # 1 -> send follow-up 1, 2 -> follow-up 2
        if step > len(config.SEQUENCE_DAYS):
            continue
        due = date.fromisoformat(r["first_email_date"]) + timedelta(days=config.SEQUENCE_DAYS[step - 1])
        text = r["follow_up_1"] if step == 1 else r["follow_up_2"]
        if due <= today and text:
            out.append(r)
    return out


# ---------------- sending ----------------
def _after_send(conn, lead, step: int, msg: EmailMessage) -> None:
    today = date.today()
    new_step = step + 1
    fields = {"email_step": new_step, "last_message_id": msg["Message-ID"], "email_approved": 0,
              "outreach_status": "Contacted", "last_contacted": today.isoformat()}
    if step == 0:
        fields["first_email_date"] = today.isoformat()
    # next follow-up date (or a final manual check-in after the last email)
    if new_step <= len(config.SEQUENCE_DAYS):
        nxt = today + timedelta(days=config.SEQUENCE_DAYS[new_step - 1] -
                                (0 if step == 0 else config.SEQUENCE_DAYS[step - 1]))
    else:
        nxt = today + timedelta(days=config.FOLLOW_UP_DAYS)
    fields["follow_up_date"] = nxt.isoformat()
    db.update_lead(conn, lead["id"], fields)
    kind = "email_sent" if step == 0 else "followup_sent"
    label = "first email" if step == 0 else f"follow-up {step}"
    db.log_activity(conn, lead["id"], kind, f"{label} to {lead['email']}: {msg['Subject']}")
    conn.commit()


def send_all(conn, dry_run: bool = False, sleep=time.sleep) -> dict:
    """Send due follow-ups first, then new first emails, up to the daily limit."""
    room = max(0, config.DAILY_EMAIL_LIMIT - sent_today(conn))
    jobs = [(l, l["email_step"]) for l in followup_queue(conn)]
    jobs += [(l, 0) for l in approved_queue(conn)]
    jobs = jobs[:room]
    result = {"first_emails": 0, "follow_ups": 0, "failed": [], "dry_run": dry_run,
              "waiting_for_tomorrow": max(0, len(followup_queue(conn)) + len(approved_queue(conn)) - room)}
    if not jobs:
        return result
    if dry_run:
        result["preview"] = [(l["lead_code"], l["email"], "first" if s == 0 else f"follow-up {s}")
                             for l, s in jobs]
        return result
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not set in .env")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        for n, (lead, step) in enumerate(jobs):
            msg = build_message(lead, step)
            try:
                smtp.send_message(msg)
                _after_send(conn, lead, step, msg)
                result["first_emails" if step == 0 else "follow_ups"] += 1
            except smtplib.SMTPException as e:
                db.log_activity(conn, lead["id"], "email_failed", str(e))
                conn.commit()
                result["failed"].append((lead["lead_code"], str(e)))
            if n < len(jobs) - 1:
                sleep(random.randint(*config.SEND_GAP_SECONDS))  # natural pacing
    return result


# kept for the dashboard button / older callers
send_approved = send_all

"""Reads your Gmail inbox (IMAP, same App Password) to stop sequences on replies,
honour opt-outs and catch bounced addresses."""
import email
import imaplib
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr

import config
import db

OPT_OUT_WORDS = ("unsubscribe", "no thanks", "no thank you", "not interested", "remove me",
                 "stop emailing", "don't contact", "do not contact", "please stop")
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _text(msg) -> str:
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8",
                                                                   errors="replace"))
            except Exception:
                pass
    return "\n".join(parts)


def _first_lines(body: str) -> str:
    """The reply itself, without the quoted original email."""
    keep = []
    for line in body.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:$", line.strip()):
            break
        keep.append(line)
    return "\n".join(keep).strip()


def classify(conn, sender: str, subject: str, body: str, msg_id: str) -> str | None:
    """Apply one inbox message to the CRM. Returns what happened, or None if unrelated."""
    if conn.execute("SELECT 1 FROM activities WHERE kind IN ('reply','bounce','opt_out') AND detail LIKE ?",
                    (f"%{msg_id}%",)).fetchone():
        return None  # already processed
    s = sender.lower()
    if any(b in s for b in BOUNCE_SENDERS):
        for addr in EMAIL_RE.findall(body):
            lead = conn.execute("SELECT * FROM leads WHERE lower(email) = ? AND email_step > 0",
                                (addr.lower(),)).fetchone()
            if lead:
                db.update_lead(conn, lead["id"], {"do_not_contact": 1, "response_status": "Wrong contact",
                                                  "outreach_status": "Closed - not interested"})
                db.log_activity(conn, lead["id"], "bounce", f"Bounced {addr} [{msg_id}]")
                return "bounce"
        return None
    lead = conn.execute("SELECT * FROM leads WHERE lower(email) = ? AND email_step > 0", (s,)).fetchone()
    if not lead:  # someone else at the same company replied
        dom = s.split("@")[-1]
        lead = conn.execute("SELECT * FROM leads WHERE domain = ? AND email_step > 0", (dom,)).fetchone()
    if not lead:
        return None
    reply = _first_lines(body)
    low = (subject + "\n" + reply).lower()
    if any(w in low for w in OPT_OUT_WORDS) and len(reply) < 400:
        db.update_lead(conn, lead["id"], {"do_not_contact": 1, "response_status": "Not interested",
                                          "outreach_status": "Closed - not interested"})
        db.log_activity(conn, lead["id"], "opt_out", f"{reply[:300]} [{msg_id}]")
        return "opt_out"
    db.update_lead(conn, lead["id"], {"outreach_status": "Replied", "response_status": "Asked for details",
                                      "whatsapp_due": 1 if lead["phone"] else 0,
                                      "follow_up_date": date.today().isoformat()})
    db.log_activity(conn, lead["id"], "reply", f"{reply[:500]} [{msg_id}]")
    return "reply"


def check_replies(conn, days: int = 21) -> dict:
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        return {"skipped": "SMTP_USER / SMTP_PASSWORD not set"}
    counts = {"reply": 0, "opt_out": 0, "bounce": 0}
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(config.SMTP_USER, config.SMTP_PASSWORD)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f'(SINCE "{since}")')
        for num in data[0].split():
            _, raw = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(raw[0][1])
            sender = parseaddr(msg.get("From", ""))[1]
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            msg_id = msg.get("Message-ID", f"imap-{num.decode()}")
            what = classify(conn, sender, subject, _text(msg), msg_id)
            if what:
                counts[what] += 1
        conn.commit()
    return counts

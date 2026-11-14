"""Helper commands for the daily Claude scheduled task (research + Gmail connector).

python claude_ops.py known                     -> companies already in CRM / rejected (do not repeat)
python claude_ops.py segments                  -> today's industry/city segments to search
python claude_ops.py add-leads FILE.json       -> add researched leads (dedupe, auto-approve)
python claude_ops.py reject "Name" "domain" "reason"
python claude_ops.py queue                     -> JSON list of emails to send today (follow-ups + first emails, capped)
python claude_ops.py mark-sent ID THREAD_ID STEP   (STEP 0 = first email, 1 = follow-up 1, 2 = follow-up 2)
python claude_ops.py active-threads            -> JSON of leads in sequence with their Gmail thread ids
python claude_ops.py reply ID "reply text"     -> lead replied: stop sequence, flag WhatsApp
python claude_ops.py optout ID "text"          -> lead said no: never contact again
python claude_ops.py bounce EMAIL              -> address bounced
python claude_ops.py report [researched] [rejected]
"""
import json
import sys
from datetime import date, timedelta

import config
import db
import outreach
import report
import research

db.init_db()
c = db.connect()
cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("help", [])


def thread_of(lead):
    m = lead["last_message_id"] or ""
    return m.split("gmail-thread:", 1)[1] if m.startswith("gmail-thread:") else None


if cmd == "known":
    print("\n".join(db.known_companies(c)) or "(none)")
elif cmd == "segments":
    for ind, loc in research.pick_segments(c, config.SEGMENTS_PER_DAY):
        print(f"{ind} | {loc}")
        c.execute("INSERT INTO search_log (run_date, industry, location) VALUES (?,?,?)",
                  (date.today().isoformat(), ind, loc))
    c.commit()
elif cmd == "add-leads":
    leads = json.load(open(args[0], encoding="utf-8"))
    for d in leads:
        if db.find_duplicate(c, d["company_name"], d.get("website"), d.get("email"),
                             d.get("instagram"), d.get("linkedin")):
            print("duplicate skipped:", d["company_name"])
            continue
        d["_source"] = "daily research (Claude)"
        print("added", db.insert_lead(c, d), d["company_name"], d.get("priority"))
    c.commit()
    print("auto-approved:", outreach.auto_approve(c))
elif cmd == "reject":
    c.execute("INSERT INTO rejected (run_date, company_name, domain, reason) VALUES (?,?,?,?)",
              (date.today().isoformat(), args[0], args[1] or None, args[2]))
    c.commit()
elif cmd == "queue":
    room = max(0, config.DAILY_EMAIL_LIMIT - outreach.sent_today(c))
    jobs = []
    for l in outreach.followup_queue(c):
        step = l["email_step"]
        body = l["follow_up_1"] if step == 1 else l["follow_up_2"]
        jobs.append({"id": l["id"], "step": step, "to": l["email"], "thread_id": thread_of(l),
                     "subject": "Re: " + (l["email_subject"] or ""), "body": outreach._with_footer(body)})
    for l in outreach.approved_queue(c):
        jobs.append({"id": l["id"], "step": 0, "to": l["email"], "thread_id": None,
                     "subject": l["email_subject"], "body": outreach._with_footer(l["email_body"])})
    print(json.dumps(jobs[:room], ensure_ascii=False, indent=1))
elif cmd == "mark-sent":
    lead_id, thread, step = int(args[0]), args[1], int(args[2])
    l = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    t = date.today()
    new_step = step + 1
    fields = {"email_step": new_step, "last_message_id": "gmail-thread:" + thread, "email_approved": 0,
              "outreach_status": "Contacted", "last_contacted": t.isoformat()}
    if step == 0:
        fields["first_email_date"] = t.isoformat()
    first = date.fromisoformat(fields.get("first_email_date") or l["first_email_date"])
    if new_step <= len(config.SEQUENCE_DAYS):
        fields["follow_up_date"] = (first + timedelta(days=config.SEQUENCE_DAYS[new_step - 1])).isoformat()
    else:
        fields["follow_up_date"] = (t + timedelta(days=config.FOLLOW_UP_DAYS)).isoformat()
    db.update_lead(c, lead_id, fields)
    db.log_activity(c, lead_id, "email_sent" if step == 0 else "followup_sent",
                    f"{'first email' if step == 0 else 'follow-up ' + str(step)} to {l['email']} (Gmail thread {thread})")
    c.commit()
    print("ok")
elif cmd == "active-threads":
    rows = c.execute("""SELECT * FROM leads WHERE email_step >= 1 AND do_not_contact = 0
                        AND outreach_status IN ('Contacted','Follow-up due')""").fetchall()
    print(json.dumps([{"id": r["id"], "company": r["company_name"], "email": r["email"],
                       "thread_id": thread_of(r)} for r in rows], indent=1))
elif cmd == "reply":
    l = c.execute("SELECT * FROM leads WHERE id=?", (int(args[0]),)).fetchone()
    db.update_lead(c, l["id"], {"outreach_status": "Replied", "response_status": "Asked for details",
                                "whatsapp_due": 1 if l["phone"] else 0, "follow_up_date": date.today().isoformat()})
    db.log_activity(c, l["id"], "reply", args[1][:500] if len(args) > 1 else "")
    c.commit(); print("ok")
elif cmd == "optout":
    db.update_lead(c, int(args[0]), {"do_not_contact": 1, "response_status": "Not interested",
                                     "outreach_status": "Closed - not interested"})
    db.log_activity(c, int(args[0]), "opt_out", args[1][:300] if len(args) > 1 else "")
    c.commit(); print("ok")
elif cmd == "bounce":
    l = c.execute("SELECT * FROM leads WHERE lower(email)=?", (args[0].lower(),)).fetchone()
    if l:
        db.update_lead(c, l["id"], {"do_not_contact": 1, "response_status": "Wrong contact",
                                    "outreach_status": "Closed - not interested"})
        db.log_activity(c, l["id"], "bounce", args[0])
        c.commit(); print("ok", l["company_name"])
    else:
        print("no lead with that email")
elif cmd == "whatsapp-due":
    # leads who replied by email and have a phone: WhatsApp them (Bird template)
    out = []
    for r in c.execute("SELECT * FROM leads WHERE whatsapp_due = 1 AND do_not_contact = 0").fetchall():
        digits = "".join(ch for ch in (r["phone"] or "") if ch.isdigit())
        if digits.startswith("0"):
            digits = digits.lstrip("0")
        if len(digits) == 10:
            digits = "91" + digits
        if len(digits) >= 11:
            out.append({"id": r["id"], "company": r["company_name"], "phone": "+" + digits,
                        "topic": (json.loads(r["recommended_services"] or "[]") or ["your marketing"])[0]})
    print(json.dumps(out, indent=1))
elif cmd == "whatsapp-sent":
    db.update_lead(c, int(args[0]), {"whatsapp_due": 0})
    db.log_activity(c, int(args[0]), "whatsapp_sent", "Sent via Bird WhatsApp template")
    c.commit(); print("ok")
elif cmd == "report":
    db.refresh_follow_ups(c); c.commit()
    stats = {"researched": int(args[0]) if args else 0, "rejected": int(args[1]) if len(args) > 1 else 0}
    data = report.build_report(c, run_stats=stats)
    print(report.save_html(data))
    print(json.dumps({k: v for k, v in data.items() if k != "top_leads"}, indent=1))
else:
    print(__doc__)

"""INFLOTIVE CRM — local web app.  Run:  python app.py  then open http://127.0.0.1:5055"""
import csv
import io
import json
from datetime import date
from urllib.parse import quote

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   url_for)

import config
import db
import outreach

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

FILTERS = {
    "all": ("All leads", "1=1", []),
    "awaiting": ("Awaiting approval", "outreach_status = 'New'", []),
    "approved": ("Approved to email", "outreach_status = 'Approved'", []),
    "online": ("Online – India", "opportunity_type = 'Online - India'", []),
    "offline": ("Offline – Delhi NCR", "opportunity_type = 'Offline - Delhi NCR'", []),
    "realestate": ("Real estate", "lower(industry) LIKE '%real estate%'", []),
    "other": ("Other industries", "lower(industry) NOT LIKE '%real estate%'", []),
    "high": ("High priority", "priority = 'High'", []),
    "medium": ("Medium priority", "priority = 'Medium'", []),
    "low": ("Low priority", "priority = 'Low'", []),
    "contacted": ("Contacted", "outreach_status IN ('Contacted','Follow-up due','Replied','Meeting booked')", []),
    "followup": ("Follow-up due", "outreach_status = 'Follow-up due'", []),
    "interested": ("Interested", "response_status IN ('Interested','Asked for details') OR outreach_status IN ('Meeting booked','Won')", []),
    "notinterested": ("Not interested", "response_status IN ('Not interested','Unsubscribed') OR outreach_status = 'Closed - not interested'", []),
}


@app.template_filter("services")
def _services(v):
    try:
        return json.loads(v or "[]")
    except json.JSONDecodeError:
        return [v] if v else []


@app.template_filter("wa")
def _wa(lead):
    digits = "".join(ch for ch in (lead["phone"] or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(lead['whatsapp_message'] or '')}" if digits else ""


def conn():
    db.init_db()
    return db.connect()


@app.route("/")
def dashboard():
    c = conn()
    db.refresh_follow_ups(c)
    c.commit()
    q = lambda sql, *a: c.execute(sql, a).fetchone()[0]
    kpi = {
        "total": q("SELECT COUNT(*) FROM leads"),
        "today": q("SELECT COUNT(*) FROM leads WHERE date_added = ?", date.today().isoformat()),
        "high": q("SELECT COUNT(*) FROM leads WHERE priority='High'"),
        "awaiting": q("SELECT COUNT(*) FROM leads WHERE outreach_status='New'"),
        "approved": q("SELECT COUNT(*) FROM leads WHERE outreach_status='Approved'"),
        "contacted": q("SELECT COUNT(*) FROM leads WHERE last_contacted IS NOT NULL"),
        "followup": q("SELECT COUNT(*) FROM leads WHERE outreach_status='Follow-up due'"),
        "interested": q("SELECT COUNT(*) FROM leads WHERE response_status IN ('Interested','Asked for details')"),
        "sent_today": outreach.sent_today(c),
        "limit": config.DAILY_EMAIL_LIMIT,
    }
    rep = c.execute("SELECT data FROM daily_reports ORDER BY run_date DESC LIMIT 1").fetchone()
    report = json.loads(rep["data"]) if rep else None
    follow = c.execute("""SELECT * FROM leads WHERE outreach_status='Follow-up due'
                          AND COALESCE(email_step,0) NOT IN (1,2)
                          ORDER BY follow_up_date LIMIT 15""").fetchall()
    whatsapp = c.execute("""SELECT * FROM leads WHERE whatsapp_due = 1
                            ORDER BY follow_up_date LIMIT 20""").fetchall()
    kpi["in_sequence"] = q("""SELECT COUNT(*) FROM leads WHERE email_step IN (1,2)
        AND do_not_contact=0 AND outreach_status IN ('Contacted','Follow-up due')""")
    kpi["replied"] = q("SELECT COUNT(*) FROM leads WHERE outreach_status='Replied'")
    newest = c.execute("""SELECT * FROM leads WHERE outreach_status='New'
        ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, id DESC LIMIT 15""").fetchall()
    return render_template("dashboard.html", kpi=kpi, report=report, follow=follow, newest=newest,
                           whatsapp=whatsapp, auto=config.AUTO_SEND,
                           auto_pr=", ".join(config.AUTO_SEND_PRIORITIES),
                           smtp_ready=bool(config.SMTP_USER and config.SMTP_PASSWORD))


@app.route("/leads")
def leads():
    key = request.args.get("f", "all")
    label, where, params = FILTERS.get(key, FILTERS["all"])
    text = request.args.get("q", "").strip()
    sql = f"SELECT * FROM leads WHERE ({where})"
    params = list(params)
    if text:
        sql += " AND (company_name LIKE ? OR city LIKE ? OR industry LIKE ? OR lead_code LIKE ?)"
        params += [f"%{text}%"] * 4
    sql += " ORDER BY id DESC"
    rows = conn().execute(sql, params).fetchall()
    return render_template("leads.html", rows=rows, filters=FILTERS, current=key, label=label, q=text)


@app.route("/lead/<int:lead_id>", methods=["GET", "POST"])
def lead(lead_id):
    c = conn()
    row = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            fields = {k: request.form.get(k) for k in (
                "company_name", "industry", "city", "website", "email", "phone", "instagram",
                "linkedin", "priority", "outreach_channel", "email_subject", "email_body",
                "follow_up_1", "follow_up_2",
                "linkedin_message", "instagram_dm", "whatsapp_message", "outreach_status",
                "response_status", "follow_up_date", "notes", "marketing_opportunity")
                if k in request.form}
            if fields.get("response_status") in ("Not interested", "Unsubscribed"):
                fields["do_not_contact"] = 1
                fields["email_approved"] = 0
            db.update_lead(c, lead_id, fields)
            db.log_activity(c, lead_id, "edit", "Lead updated in CRM")
            flash("Saved.")
        elif action == "approve_email":
            if not row["email"]:
                flash("No email address on this lead — cannot approve email.")
            else:
                db.update_lead(c, lead_id, {"email_approved": 1, "outreach_status": "Approved"})
                db.log_activity(c, lead_id, "status", "Email approved for sending")
                flash("Email approved. It will go out when you click 'Send approved emails'.")
        elif action == "unapprove":
            db.update_lead(c, lead_id, {"email_approved": 0, "outreach_status": "New"})
            db.log_activity(c, lead_id, "status", "Approval withdrawn")
        elif action in ("linkedin", "instagram", "whatsapp", "call", "meeting"):
            db.mark_contacted(c, lead_id, action, request.form.get("detail", ""))
            if action == "whatsapp":
                db.update_lead(c, lead_id, {"whatsapp_due": 0})
            flash(f"Logged {action} contact; follow-up set in {config.FOLLOW_UP_DAYS} days.")
        elif action == "note":
            db.log_activity(c, lead_id, "note", request.form.get("detail", ""))
        c.commit()
        return redirect(url_for("lead", lead_id=lead_id))
    acts = c.execute("SELECT * FROM activities WHERE lead_id = ? ORDER BY id DESC", (lead_id,)).fetchall()
    return render_template("lead.html", l=row, acts=acts, statuses=config.OUTREACH_STATUSES,
                           responses=config.RESPONSE_STATUSES)


@app.route("/lead/new", methods=["GET", "POST"])
def new_lead():
    if request.method == "POST":
        c = conn()
        f = {k: (v.strip() or None) for k, v in request.form.items()}
        if not f.get("company_name"):
            flash("Company name is required.")
            return redirect(url_for("new_lead"))
        dup = db.find_duplicate(c, f["company_name"], f.get("website"), f.get("email"),
                                f.get("instagram"), f.get("linkedin"))
        if dup:
            flash(f"Duplicate: already in CRM as {dup['lead_code']}.")
            return redirect(url_for("lead", lead_id=dup["id"]))
        f["_source"] = "manual entry"
        new_id = db.insert_lead(c, f)
        c.commit()
        return redirect(url_for("lead", lead_id=new_id))
    return render_template("new_lead.html")


def _send_in_background():
    c = conn()
    try:
        print("[crm] send:", outreach.send_all(c))
    except Exception as e:  # shown in the console window
        print("[crm] send failed:", e)


@app.post("/send-approved")
def send_approved():
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        flash("SMTP_USER / SMTP_PASSWORD are not set in .env")
        return redirect(url_for("dashboard"))
    import threading
    c = conn()
    outreach.auto_approve(c)
    preview = outreach.send_all(c, dry_run=True)
    n = len(preview.get("preview", []))
    threading.Thread(target=_send_in_background, daemon=True).start()
    flash(f"Sending {n} email(s) now, a minute or two apart. Refresh to see progress.")
    return redirect(url_for("dashboard"))


@app.route("/reports")
def reports():
    rows = conn().execute("SELECT run_date, data FROM daily_reports ORDER BY run_date DESC").fetchall()
    reps = [json.loads(r["data"]) for r in rows]
    sel = request.args.get("d")
    current = next((r for r in reps if r["run_date"] == sel), reps[0] if reps else None)
    return render_template("reports.html", reps=reps, r=current)


@app.post("/reports/build")
def build_report_now():
    import report
    c = conn()
    report.save_html(report.build_report(c))
    flash("Today's report rebuilt.")
    return redirect(url_for("reports"))


EXPORT_COLS = ["lead_code", "company_name", "industry", "city", "opportunity_type", "website",
               "email", "email_verified", "phone", "phone_verified", "instagram", "linkedin",
               "founded", "description", "digital_presence", "marketing_opportunity",
               "recommended_services", "reason_for_targeting", "priority", "priority_reason",
               "outreach_channel", "email_subject", "email_body", "linkedin_message",
               "instagram_dm", "whatsapp_message", "date_added", "outreach_status",
               "last_contacted", "follow_up_date", "response_status", "notes"]


@app.route("/export.csv")
def export_csv():
    rows = conn().execute("SELECT * FROM leads ORDER BY id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLS)
    for r in rows:
        w.writerow([r[c] for c in EXPORT_COLS])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=inflotive_leads_{date.today()}.csv"})


@app.route("/import", methods=["GET", "POST"])
def import_csv():
    """Import a CSV (e.g. File > Download > CSV from your Google Sheet)."""
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            flash("Choose a CSV file.")
            return redirect(url_for("import_csv"))
        text = file.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        alias = {"company": "company_name", "company name": "company_name", "name": "company_name",
                 "location": "city", "instagram profile": "instagram", "linkedin profile": "linkedin",
                 "notes": "notes", "lead priority": "priority", "phone number": "phone"}
        c = conn()
        added = dup = 0
        for raw in reader:
            rec = {}
            for k, v in raw.items():
                if not k:
                    continue
                key = k.strip().lower()
                key = alias.get(key, key.replace(" ", "_"))
                if key in db.LEAD_COLUMNS and v and v.strip():
                    rec[key] = v.strip()
            if not rec.get("company_name"):
                continue
            if db.find_duplicate(c, rec["company_name"], rec.get("website"), rec.get("email"),
                                 rec.get("instagram"), rec.get("linkedin")):
                dup += 1
                continue
            rec.pop("lead_code", None)
            rec["_source"] = "CSV import"
            db.insert_lead(c, rec)
            added += 1
        c.commit()
        flash(f"Imported {added} lead(s); skipped {dup} duplicate(s).")
        return redirect(url_for("leads"))
    return render_template("import.html")


if __name__ == "__main__":
    db.init_db()
    print(f"INFLOTIVE CRM running at http://127.0.0.1:{config.CRM_PORT}")
    app.run(host="127.0.0.1", port=config.CRM_PORT, debug=False)

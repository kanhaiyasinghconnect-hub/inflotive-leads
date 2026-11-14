"""Daily research report (stored in the CRM and saved as HTML)."""
import html
import json
from collections import Counter
from datetime import date, datetime

import config


def build_report(conn, run_date: str | None = None, run_stats: dict | None = None) -> dict:
    run_date = run_date or date.today().isoformat()
    rows = conn.execute("SELECT * FROM leads WHERE date_added = ?", (run_date,)).fetchall()
    services = Counter()
    for r in rows:
        try:
            services.update(json.loads(r["recommended_services"] or "[]"))
        except json.JSONDecodeError:
            pass
    pr = Counter(r["priority"] for r in rows)
    drafted = sum(1 for r in rows for f in ("email_body", "linkedin_message", "instagram_dm",
                                              "whatsapp_message") if r[f])
    follow = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE outreach_status = 'Follow-up due' AND do_not_contact = 0"
    ).fetchone()[0]
    awaiting = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE outreach_status = 'New' AND priority IN ('High','Medium')"
    ).fetchone()[0]
    stats = run_stats or {}

    def acts(kind):
        return conn.execute("SELECT COUNT(*) FROM activities WHERE kind = ? AND ts LIKE ?",
                            (kind, run_date + "%")).fetchone()[0]

    data = {
        "first_emails_sent": acts("email_sent"),
        "follow_ups_sent": acts("followup_sent"),
        "replies": acts("reply"),
        "opt_outs": acts("opt_out"),
        "bounces": acts("bounce"),
        "whatsapp_due": conn.execute(
            "SELECT COUNT(*) FROM leads WHERE whatsapp_due = 1").fetchone()[0],
        "in_sequence": conn.execute(
            """SELECT COUNT(*) FROM leads WHERE email_step IN (1,2) AND do_not_contact = 0
               AND outreach_status IN ('Contacted','Follow-up due')""").fetchone()[0],
        "run_date": run_date,
        "companies_researched": stats.get("researched", len(rows)),
        "qualified_leads": len(rows),
        "duplicates_skipped": stats.get("duplicates_skipped", 0),
        "rejected": stats.get("rejected", 0),
        "high": pr.get("High", 0), "medium": pr.get("Medium", 0), "low": pr.get("Low", 0),
        "by_industry": Counter(r["industry"] for r in rows).most_common(),
        "by_city": Counter(r["city"] for r in rows).most_common(),
        "online_opportunities": sum(1 for r in rows if r["opportunity_type"] == "Online - India"),
        "offline_ncr_opportunities": sum(1 for r in rows if r["opportunity_type"] == "Offline - Delhi NCR"),
        "recommended_services": services.most_common(),
        "messages_prepared": drafted,
        "follow_ups_pending": follow,
        "awaiting_approval": awaiting,
        "segments": stats.get("segments", []),
        "top_leads": [{"code": r["lead_code"], "company": r["company_name"], "city": r["city"],
                       "industry": r["industry"], "priority": r["priority"],
                       "opportunity": r["marketing_opportunity"]}
                      for r in sorted(rows, key=lambda r: {"High": 0, "Medium": 1}.get(r["priority"], 2))[:10]],
    }
    conn.execute("INSERT OR REPLACE INTO daily_reports (run_date, data, created_at) VALUES (?,?,?)",
                 (run_date, json.dumps(data), datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return data


def save_html(data: dict) -> str:
    config.REPORTS_DIR.mkdir(exist_ok=True)
    e = html.escape

    def table(pairs):
        return "".join(f"<tr><td>{e(str(k))}</td><td>{v}</td></tr>" for k, v in pairs) or "<tr><td>—</td></tr>"

    leads = "".join(
        f"<tr><td>{e(l['code'])}</td><td>{e(l['company'])}</td><td>{e(l['industry'] or '')}</td>"
        f"<td>{e(l['city'] or '')}</td><td>{e(l['priority'] or '')}</td><td>{e(l['opportunity'] or '')}</td></tr>"
        for l in data["top_leads"])
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>INFLOTIVE daily report {data['run_date']}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1d1d1f}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;font-size:14px}}
.k{{display:inline-block;margin:4px 12px 4px 0;padding:10px 14px;background:#f4f4f6;border-radius:8px}}</style></head><body>
<h1>INFLOTIVE — Daily lead report, {data['run_date']}</h1>
<div>
<span class="k">Researched: <b>{data['companies_researched']}</b></span>
<span class="k">Qualified: <b>{data['qualified_leads']}</b></span>
<span class="k">High: <b>{data['high']}</b></span><span class="k">Medium: <b>{data['medium']}</b></span>
<span class="k">Low: <b>{data['low']}</b></span>
<span class="k">Online (India): <b>{data['online_opportunities']}</b></span>
<span class="k">Offline (Delhi NCR): <b>{data['offline_ncr_opportunities']}</b></span>
<span class="k">Messages prepared: <b>{data['messages_prepared']}</b></span>
<span class="k">Follow-ups pending: <b>{data['follow_ups_pending']}</b></span>
<span class="k">Awaiting your approval: <b>{data['awaiting_approval']}</b></span>
<span class="k">First emails sent: <b>{data['first_emails_sent']}</b></span>
<span class="k">Follow-ups sent: <b>{data['follow_ups_sent']}</b></span>
<span class="k">Replies: <b>{data['replies']}</b></span>
<span class="k">Opt-outs: <b>{data['opt_outs']}</b></span>
<span class="k">Bounces: <b>{data['bounces']}</b></span>
<span class="k">WhatsApp now: <b>{data['whatsapp_due']}</b></span>
</div>
<h2>Top leads</h2><table><tr><th>ID</th><th>Company</th><th>Industry</th><th>City</th><th>Priority</th><th>Opportunity</th></tr>{leads}</table>
<h2>By industry</h2><table>{table(data['by_industry'])}</table>
<h2>By city</h2><table>{table(data['by_city'])}</table>
<h2>Recommended services</h2><table>{table(data['recommended_services'])}</table>
</body></html>"""
    path = config.REPORTS_DIR / f"report_{data['run_date']}.html"
    path.write_text(page, encoding="utf-8")
    return str(path)

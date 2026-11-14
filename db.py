"""SQLite lead database (the INFLOTIVE CRM) with duplicate detection."""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_code TEXT UNIQUE,
    company_name TEXT NOT NULL,
    industry TEXT,
    city TEXT,
    opportunity_type TEXT,          -- 'Offline - Delhi NCR' or 'Online - India'
    website TEXT,
    domain TEXT,
    email TEXT,
    email_verified INTEGER DEFAULT 0,
    phone TEXT,
    phone_verified INTEGER DEFAULT 0,
    instagram TEXT,
    linkedin TEXT,
    founded TEXT,
    founded_verified INTEGER DEFAULT 0,
    description TEXT,
    digital_presence TEXT,
    marketing_opportunity TEXT,
    recommended_services TEXT,      -- JSON list
    reason_for_targeting TEXT,
    outreach_channel TEXT,
    priority TEXT,                  -- High / Medium / Low
    priority_reason TEXT,
    email_subject TEXT,
    email_body TEXT,
    linkedin_message TEXT,
    instagram_dm TEXT,
    whatsapp_message TEXT,
    sources TEXT,                   -- JSON list of URLs
    date_added TEXT,
    outreach_status TEXT DEFAULT 'New',
    email_approved INTEGER DEFAULT 0,
    last_contacted TEXT,
    follow_up_date TEXT,
    response_status TEXT DEFAULT 'No response',
    do_not_contact INTEGER DEFAULT 0,
    notes TEXT,
    name_key TEXT
);
CREATE INDEX IF NOT EXISTS ix_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS ix_leads_name ON leads(name_key);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    ts TEXT,
    kind TEXT,          -- email_sent, linkedin_sent, instagram_sent, call, note, status
    detail TEXT
);

CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    industry TEXT,
    location TEXT
);

CREATE TABLE IF NOT EXISTS daily_reports (
    run_date TEXT PRIMARY KEY,
    data TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS rejected (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT,
    company_name TEXT,
    domain TEXT,
    reason TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


NEW_COLUMNS = {  # added after v1; applied to existing databases automatically
    "follow_up_1": "TEXT",
    "follow_up_2": "TEXT",
    "email_step": "INTEGER DEFAULT 0",      # 0 none, 1 first sent, 2 follow-up 1, 3 follow-up 2
    "first_email_date": "TEXT",
    "last_message_id": "TEXT",
    "whatsapp_due": "INTEGER DEFAULT 0",    # replied/opted in -> WhatsApp them now
}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        for col, typ in NEW_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {typ}")


# ---------------- normalisation / dedupe ----------------
_SUFFIXES = r"\b(pvt|private|ltd|limited|llp|inc|co|company|india|the|realty|group)\b"


def name_key(name: str) -> str:
    n = (name or "").lower()
    n = re.sub(_SUFFIXES, " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # social / directory hosts are not a company's own domain
    if not host or any(s in host for s in ("instagram.com", "facebook.com", "linkedin.com",
                                           "justdial.com", "indiamart.com", "google.com")):
        return None
    return host


def find_duplicate(conn, company_name, website=None, email=None, instagram=None, linkedin=None):
    """Return the existing lead row that matches, or None."""
    dom = domain_of(website)
    checks = []
    if dom:
        checks.append(("domain = ?", dom))
    nk = name_key(company_name)
    if nk:
        checks.append(("name_key = ?", nk))
    if email:
        checks.append(("lower(email) = ?", email.lower().strip()))
    if instagram:
        checks.append(("lower(instagram) = ?", instagram.lower().strip().rstrip("/")))
    if linkedin:
        checks.append(("lower(linkedin) = ?", linkedin.lower().strip().rstrip("/")))
    for clause, val in checks:
        row = conn.execute(f"SELECT * FROM leads WHERE {clause} LIMIT 1", (val,)).fetchone()
        if row:
            return row
    return None


def known_companies(conn, limit: int = 600) -> list[str]:
    rows = conn.execute(
        "SELECT company_name, city, domain FROM leads ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    rej = conn.execute(
        "SELECT company_name, domain FROM rejected ORDER BY id DESC LIMIT 300"
    ).fetchall()
    out = [f"{r['company_name']} ({r['city'] or ''}) {r['domain'] or ''}".strip() for r in rows]
    out += [f"{r['company_name']} {r['domain'] or ''}".strip() for r in rej]
    return out


def next_lead_code(conn) -> str:
    row = conn.execute("SELECT MAX(id) AS m FROM leads").fetchone()
    return f"INF-{(row['m'] or 0) + 1:05d}"


def insert_lead(conn, lead: dict) -> int:
    lead = dict(lead)
    lead.setdefault("date_added", date.today().isoformat())
    lead["lead_code"] = next_lead_code(conn)
    lead["domain"] = domain_of(lead.get("website"))
    lead["name_key"] = name_key(lead["company_name"])
    if isinstance(lead.get("recommended_services"), list):
        lead["recommended_services"] = json.dumps(lead["recommended_services"])
    if isinstance(lead.get("sources"), list):
        lead["sources"] = json.dumps(lead["sources"])
    lead["opportunity_type"] = ("Offline - Delhi NCR" if config.is_ncr(lead.get("city"))
                                else "Online - India")
    cols = [c for c in lead if c in LEAD_COLUMNS]
    sql = f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    cur = conn.execute(sql, [lead[c] for c in cols])
    log_activity(conn, cur.lastrowid, "created", f"Added by {lead.get('_source', 'research')}")
    return cur.lastrowid


def update_lead(conn, lead_id: int, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if k in LEAD_COLUMNS and k != "id"}
    if not fields:
        return
    if "website" in fields:
        fields["domain"] = domain_of(fields["website"])
    if "city" in fields:
        fields["opportunity_type"] = ("Offline - Delhi NCR" if config.is_ncr(fields["city"])
                                      else "Online - India")
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE leads SET {sets} WHERE id = ?", [*fields.values(), lead_id])


def log_activity(conn, lead_id, kind, detail=""):
    conn.execute("INSERT INTO activities (lead_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
                 (lead_id, datetime.now().isoformat(timespec="seconds"), kind, detail))


def mark_contacted(conn, lead_id: int, channel: str, detail: str = "") -> None:
    today = date.today()
    conn.execute(
        """UPDATE leads SET outreach_status = 'Contacted', last_contacted = ?,
           follow_up_date = ? WHERE id = ?""",
        (today.isoformat(), (today + timedelta(days=config.FOLLOW_UP_DAYS)).isoformat(), lead_id),
    )
    log_activity(conn, lead_id, f"{channel}_sent", detail)


def refresh_follow_ups(conn) -> int:
    """Flag contacted leads whose follow-up date has arrived."""
    cur = conn.execute(
        """UPDATE leads SET outreach_status = 'Follow-up due'
           WHERE outreach_status = 'Contacted' AND follow_up_date <= ?
             AND response_status IN ('No response', '') AND do_not_contact = 0""",
        (date.today().isoformat(),),
    )
    return cur.rowcount


LEAD_COLUMNS = {
    "lead_code", "company_name", "industry", "city", "opportunity_type", "website", "domain",
    "email", "email_verified", "phone", "phone_verified", "instagram", "linkedin", "founded",
    "founded_verified", "description", "digital_presence", "marketing_opportunity",
    "recommended_services", "reason_for_targeting", "outreach_channel", "priority",
    "priority_reason", "email_subject", "email_body", "linkedin_message", "instagram_dm",
    "whatsapp_message", "sources", "date_added", "outreach_status", "email_approved",
    "last_contacted", "follow_up_date", "response_status", "do_not_contact", "notes", "name_key",
    *NEW_COLUMNS,
}

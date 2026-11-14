"""Daily AI lead research: Claude searches the web, studies each business and drafts outreach.

Phase 1  research  - Claude (web_search + web_fetch) studies real businesses and writes a dossier.
Phase 2  structure - Claude converts the dossier into validated JSON (no new facts allowed).
Nothing is sent to any business here; this only researches and drafts.
"""
from __future__ import annotations

import random
from datetime import date
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

import config
import db

FALLBACK_BETA = "server-side-fallback-2026-07-01"


# ---------------- output schema ----------------
class Lead(BaseModel):
    company_name: str
    industry: str
    city: str
    website: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    instagram: Optional[str]
    linkedin: Optional[str]
    founded: Optional[str]
    founded_verified: bool
    description: str
    digital_presence: str
    marketing_opportunity: str
    recommended_services: List[str]
    reason_for_targeting: str
    outreach_channel: Literal["Email", "LinkedIn", "Instagram", "WhatsApp / Call", "In-person (NCR)"]
    priority: Literal["High", "Medium", "Low"]
    priority_reason: str
    email_subject: Optional[str]
    email_body: Optional[str]
    follow_up_1: Optional[str]
    follow_up_2: Optional[str]
    linkedin_message: Optional[str]
    instagram_dm: Optional[str]
    whatsapp_message: Optional[str]
    sources: List[str]


class Rejected(BaseModel):
    company_name: str
    website: Optional[str]
    reason: str


class LeadBatch(BaseModel):
    companies_researched: int
    leads: List[Lead]
    rejected: List[Rejected]


# ---------------- segment rotation ----------------
def pick_segments(conn, n: int) -> list[tuple[str, str]]:
    """Anchor segment (new Noida real estate) + least-recently-searched industry/city pairs."""
    used = {(r["industry"], r["location"]): r["last"] for r in conn.execute(
        "SELECT industry, location, MAX(run_date) AS last FROM search_log GROUP BY 1, 2")}
    pairs = []
    for ind in config.INDUSTRIES:
        cities = config.ONLINE_CITIES + ["Noida", "Gurugram", "Delhi"]
        for city in cities:
            pairs.append((ind, city))
    random.shuffle(pairs)
    pairs.sort(key=lambda p: used.get(p, ""))  # never-used first, then oldest
    chosen, seen_ind = [config.ANCHOR_SEGMENT], set()
    for ind, city in pairs:
        if len(chosen) >= n:
            break
        if ind in seen_ind or ind.startswith("Real estate"):
            continue  # spread across industries; anchor already covers real estate
        seen_ind.add(ind)
        chosen.append((ind, city))
    return chosen


# ---------------- Claude calls ----------------
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4, timeout=1200)


def _system() -> str:
    return config.BRIEF_PATH.read_text(encoding="utf-8")


def _research_dossier(client, segments, exclude, n_leads) -> str:
    seg_lines = "\n".join(f"- {ind} — {loc}" for ind, loc in segments)
    excl = "\n".join(exclude[:600]) or "(database is empty)"
    prompt = f"""Today is {date.today():%d %B %Y}. Run today's lead research for INFLOTIVE.

Search segments for today (spread the leads across them; the first segment is the standing priority):
{seg_lines}

Target: about {n_leads} NEW qualified leads in total. Quality over quantity — fewer strong leads beat many weak ones.

Already in our database or previously rejected — do NOT return these again:
{excl}

For every candidate:
1. Find the business with web search (Google Maps listings, company sites, news of new launches, RERA / registry pages, Instagram, LinkedIn, directories).
2. Open its official website with web_fetch (home and contact/about pages) and confirm what it does.
3. Record ONLY contact details you actually saw on the official site or an official profile — email, phone, Instagram URL, LinkedIn URL — with the URL where you saw each. Anything not seen: write "not found".
4. Founding/launch date only if a source states it; otherwise "unverified".
5. Assess digital presence (site quality, social activity, ads seen, reviews) from what you observed.
6. Identify the marketing gap, choose only the relevant INFLOTIVE services (2–5), set priority with a reason, choose the best outreach channel (In-person only for Delhi NCR).
7. Draft outreach following the brief: email subject + email body (sign off as {config.SENDER_NAME}, {config.SENDER_TITLE}), two short follow-up emails sent in the same thread if there is no reply (follow-up 1 on day {config.SEQUENCE_DAYS[0]}: 40–70 words, adds one new useful angle; follow-up 2 on day {config.SEQUENCE_DAYS[-1]}: 30–50 words, polite last note) — no subject lines for follow-ups, LinkedIn connection note (<280 chars), Instagram DM if they are active there, short WhatsApp message if a business number exists (this is only sent after they reply or opt in, so it may reference our email).

Also list companies you looked at but rejected, with the reason (e.g. already has a large agency, closed, no fit).

Write the result as a clear dossier, one section per company, with every source URL."""

    messages = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": config.MAX_WEB_SEARCHES,
         "user_location": {"type": "approximate", "country": "IN", "city": "Noida",
                           "region": "Uttar Pradesh", "timezone": "Asia/Kolkata"}},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": config.MAX_WEB_FETCHES},
    ]
    for _ in range(8):  # resume server-tool turns that pause
        with client.beta.messages.stream(
            model=config.MODEL,
            max_tokens=64000,
            system=_system(),
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"Research request was declined: {msg.stop_details}")
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        return "\n".join(b.text for b in msg.content if b.type == "text")
    raise RuntimeError("Research did not finish after several resumes")


def _structure(client, dossier: str) -> LeadBatch:
    resp = client.messages.parse(
        model=config.MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        system=_system(),
        messages=[{"role": "user", "content": (
            "Convert this research dossier into the JSON schema. Copy facts exactly; do not add, "
            "guess or complete any email, phone, URL or date. Use null for anything marked "
            "'not found' or 'unverified'. founded_verified is true only when a source states it. "
            f"Only use these service names where they fit: {', '.join(config.SERVICES)}.\n\n"
            f"<dossier>\n{dossier}\n</dossier>")}],
        output_format=LeadBatch,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Structuring request was declined: {resp.stop_details}")
    return resp.parsed_output


def run_research(conn, n_leads: int | None = None) -> dict:
    """Research, verify, dedupe and store today's leads. Returns run stats."""
    import verify  # local import keeps module load light

    n_leads = n_leads or config.LEADS_PER_DAY
    segments = pick_segments(conn, config.SEGMENTS_PER_DAY)
    client = _client()
    print(f"[research] segments: {segments}")
    dossier = _research_dossier(client, segments, db.known_companies(conn), n_leads)
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / f"dossier_{date.today()}.md").write_text(dossier, encoding="utf-8")

    batch = _structure(client, dossier)
    today = date.today().isoformat()
    for ind, loc in segments:
        conn.execute("INSERT INTO search_log (run_date, industry, location) VALUES (?, ?, ?)",
                     (today, ind, loc))
    for r in batch.rejected:
        conn.execute("INSERT INTO rejected (run_date, company_name, domain, reason) VALUES (?,?,?,?)",
                     (today, r.company_name, db.domain_of(r.website), r.reason))

    added, dupes = [], 0
    for lead in batch.leads:
        d = lead.model_dump()
        if db.find_duplicate(conn, d["company_name"], d["website"], d["email"],
                             d["instagram"], d["linkedin"]):
            dupes += 1
            continue
        checks = verify.verify_contacts(d["website"], d["email"], d["phone"])
        d["email_verified"] = int(checks["email"])
        d["phone_verified"] = int(checks["phone"])
        d["founded_verified"] = int(d["founded_verified"])
        d["_source"] = "daily AI research"
        added.append(db.insert_lead(conn, d))
    conn.commit()
    return {"segments": segments, "researched": batch.companies_researched,
            "added_ids": added, "duplicates_skipped": dupes, "rejected": len(batch.rejected)}

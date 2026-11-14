"""Central settings for the INFLOTIVE lead system. Edit freely."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "data" / "inflotive_crm.db"
REPORTS_DIR = BASE_DIR / "reports"
BRIEF_PATH = BASE_DIR / "inflotive_brief.md"

# ---------------- AI research ----------------
MODEL = os.getenv("INFLOTIVE_MODEL", "claude-opus-5-5")
LEADS_PER_DAY = int(os.getenv("LEADS_PER_DAY", "12"))
SEGMENTS_PER_DAY = int(os.getenv("SEGMENTS_PER_DAY", "4"))
MAX_WEB_SEARCHES = int(os.getenv("MAX_WEB_SEARCHES", "40"))
MAX_WEB_FETCHES = int(os.getenv("MAX_WEB_FETCHES", "40"))

# ---------------- Geography ----------------
# Offline (physical meetings) only in Delhi NCR, Noida first.
NCR_CITIES = ["Noida", "Greater Noida", "Delhi", "New Delhi", "Gurugram", "Gurgaon",
              "Ghaziabad", "Faridabad"]
ONLINE_CITIES = ["Delhi NCR", "Mumbai", "Bangalore", "Hyderabad", "Pune", "Kolkata",
                 "Chennai", "Ahmedabad", "Jaipur", "Chandigarh", "Lucknow", "Indore",
                 "Kochi", "Surat", "Nagpur"]

# ---------------- Industry rotation ----------------
# The daily run always includes the "anchor" segment, then rotates through the rest
# so the same industry/city pair is not searched again until all others have had a turn.
ANCHOR_SEGMENT = ("Newly established real estate agencies, property consultancies, "
                  "channel partners and new real estate brands", "Noida / Greater Noida")
INDUSTRIES = [
    "Real estate (new brokerages & developers)",
    "D2C / e-commerce brands",
    "Fashion & apparel brands",
    "Educational institutes & coaching centres",
    "Healthcare clinics, dental & diagnostics",
    "Restaurants, cafes & hospitality",
    "Startups & SaaS companies",
    "Interior design & architecture firms",
    "Manufacturing & B2B suppliers",
    "Coaches, consultants & personal brands",
    "Gyms, salons & wellness",
    "Professional services (CA, legal, consulting)",
]

# ---------------- Email outreach ----------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail: use an App Password
SENDER_NAME = os.getenv("SENDER_NAME", "INFLOTIVE")
SENDER_TITLE = os.getenv("SENDER_TITLE", "Business Development, INFLOTIVE")
SENDER_PHONE = os.getenv("SENDER_PHONE", "")
SENDER_WEBSITE = os.getenv("SENDER_WEBSITE", "")
SENDER_ADDRESS = os.getenv("SENDER_ADDRESS", "Noida, Uttar Pradesh, India")
DAILY_EMAIL_LIMIT = int(os.getenv("DAILY_EMAIL_LIMIT", "25"))
FOLLOW_UP_DAYS = int(os.getenv("FOLLOW_UP_DAYS", "4"))

# Fully automatic email mode (owner authorised 2026-11-12):
# leads with these priorities AND an email confirmed on their own website are emailed
# without manual approval. Everything else still waits for approval in the CRM.
AUTO_SEND = os.getenv("AUTO_SEND", "true").lower() == "true"
AUTO_SEND_PRIORITIES = [p.strip() for p in os.getenv("AUTO_SEND_PRIORITIES", "High,Medium").split(",")]
# Days after the first email to send follow-up 1 and follow-up 2 (same thread, stops on reply)
SEQUENCE_DAYS = [int(x) for x in os.getenv("SEQUENCE_DAYS", "4,9").split(",")]
SEND_GAP_SECONDS = (int(os.getenv("SEND_GAP_MIN", "45")), int(os.getenv("SEND_GAP_MAX", "120")))
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")

# ---------------- CRM web app ----------------
CRM_PORT = int(os.getenv("CRM_PORT", "5055"))
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me-inflotive")

SERVICES = [
    "Meta Ads", "Google Ads", "Performance marketing", "Retargeting",
    "B2B lead generation", "B2C lead generation", "Property lead generation",
    "Student / admission lead generation", "Landing page funnels",
    "Social media management", "LinkedIn marketing", "Content strategy",
    "Creatives & graphic design", "Reels & video production", "Product videos",
    "Website development", "E-commerce website", "Website redesign", "SEO",
    "Branding & positioning", "Marketing strategy", "Influencer marketing",
    "WhatsApp automation", "AI chatbots", "Marketing automation",
    "CRM & lead management", "Email marketing",
]

OUTREACH_STATUSES = ["New", "Approved", "Contacted", "Follow-up due", "Replied",
                     "Meeting booked", "Won", "Closed - not interested"]
RESPONSE_STATUSES = ["No response", "Interested", "Not interested", "Asked for details",
                     "Wrong contact", "Unsubscribed"]


def is_ncr(city: str | None) -> bool:
    if not city:
        return False
    c = city.lower()
    return any(n.lower() in c for n in NCR_CITIES) or "ncr" in c

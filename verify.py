"""Second check on contact details: does the email / phone actually appear on the company's site?"""
import re

import requests

PAGES = ["", "/contact", "/contact-us", "/about", "/about-us"]
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LeadVerifier/1.0)"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-10:]


def verify_contacts(website: str | None, email: str | None, phone: str | None) -> dict:
    result = {"email": False, "phone": False}
    if not website or not (email or phone):
        return result
    base = website if "://" in website else "https://" + website
    base = base.rstrip("/")
    text = ""
    for p in PAGES:
        try:
            r = requests.get(base + p, headers=HEADERS, timeout=12)
            if r.ok:
                text += r.text.lower()
        except requests.RequestException:
            continue
    if email and email.lower() in text:
        result["email"] = True
    if phone:
        d = _digits(phone)
        page_digits = re.sub(r"\D", "", text)
        result["phone"] = len(d) == 10 and d in page_digits
    return result

"""Type your keys here; they are written straight into .env (nothing is shown or sent anywhere)."""
from getpass import getpass
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
EXAMPLE = ENV.with_name(".env.example")

lines = (ENV if ENV.exists() else EXAMPLE).read_text(encoding="utf-8").splitlines()
current = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in lines if "=" in l and not l.startswith("#")}

ASK = [
    ("ANTHROPIC_API_KEY", "Claude API key (starts with sk-ant-)", True),
    ("SMTP_USER", "Your Gmail address", False),
    ("SMTP_PASSWORD", "Gmail App Password (16 letters, spaces are fine)", True),
    ("SENDER_PHONE", "Phone number for your email signature", False),
    ("SENDER_WEBSITE", "Website for your signature (Enter to skip)", False),
]

print("\nINFLOTIVE key setup - press Enter to keep an existing value.")
print("Paste with a RIGHT-CLICK inside this window. Values stay on this computer.\n")
new = {}
for key, label, secret in ASK:
    have = " [already set]" if current.get(key) else ""
    while True:
        val = input(f"{label}{have}: ").strip()
        if key == "SMTP_PASSWORD":
            val = val.replace(" ", "")
        if not val and current.get(key) and key not in ("ANTHROPIC_API_KEY", "SMTP_PASSWORD"):
            break
        if key == "ANTHROPIC_API_KEY" and not val.startswith("sk-ant-"):
            print("  That is not a Claude API key. Copy the key from console.anthropic.com "
                  "(it starts with sk-ant-) and right-click to paste.")
            continue
        if key == "SMTP_PASSWORD" and not (len(val) == 16 and val.isalpha()):
            print(f"  That is not a Gmail App Password ({len(val)} characters). Create one at "
                  "myaccount.google.com/apppasswords - it is 16 letters.")
            continue
        break
    if val:
        new[key] = val

out = []
for l in lines:
    k = l.split("=", 1)[0] if "=" in l and not l.startswith("#") else None
    out.append(f"{k}={new.pop(k)}" if k in new else l)
out += [f"{k}={v}" for k, v in new.items()]
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
final = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in out if "=" in l and not l.startswith("#")}
print(f"\nSaved to {ENV}")
api, pw = final.get("ANTHROPIC_API_KEY", ""), final.get("SMTP_PASSWORD", "")
print("  Claude API key :", "OK" if api.startswith("sk-ant-") else "MISSING or wrong (must start with sk-ant-)")
print("  Gmail App Pass :", "OK (16 letters)" if len(pw) == 16 else f"WRONG - {len(pw)} characters, an App Password has 16")
print("\nIf both say OK, close this window and tell Claude 'done'. Otherwise run it again.")
input("Press Enter to close...")

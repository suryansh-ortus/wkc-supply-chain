"""
WhatsApp stock alerts over Twilio — standalone.

    pip install twilio

Put a .env next to this file (or use real environment variables):

    TWILIO_ACCOUNT_SID=ACxxxxxxxx
    TWILIO_AUTH_TOKEN=xxxxxxxx
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

    python whatsapp_blast.py test +919876543210
    python whatsapp_blast.py blast --dry-run
    python whatsapp_blast.py blast
    python whatsapp_blast.py blast --image https://example.com/bread.jpg

Two CSVs sit next to this file. Both are created for you on first run.

    contacts.csv    name,phone
    stock.csv       product,qty,price,note

THE ONE RULE THAT TRIPS EVERYONE UP: on the Twilio sandbox you can only
message someone who has joined it, and freeform text only works for 24 hours
after they last messaged you. So every recipient must first send

    join <your-two-word-code>

to +1 415 523 8886 from their own WhatsApp. The code is on your Twilio
console page. After 24 hours of silence they must message you again, or you
need an approved template.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
CONTACTS = HERE / "contacts.csv"
STOCK = HERE / "stock.csv"
LOG = HERE / "sent_log.csv"

DEFAULT_COUNTRY = "+91"
THROTTLE_SECONDS = 3.2        # the sandbox allows one message every 3 seconds


# =============================================================================
# SETUP
# =============================================================================

def load_env() -> None:
    """Read a .env sitting next to this script. No dependency, no export."""
    path = HERE / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        # a real shell export stays authoritative
        os.environ.setdefault(key.strip(), value)


def credentials() -> tuple[str, str, str]:
    load_env()
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    sender = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886").strip()
    if not sid or not token:
        sys.exit("No credentials. Put a .env next to this script with:\n"
                 "  TWILIO_ACCOUNT_SID=AC...\n"
                 "  TWILIO_AUTH_TOKEN=...\n"
                 "  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886\n"
                 "Both values are on the Twilio console home page.")
    if not sender.startswith("whatsapp:"):
        sender = "whatsapp:" + sender
    return sid, token, sender


def seed_files() -> None:
    """First run: write example CSVs so there is something to edit."""
    if not CONTACTS.exists():
        CONTACTS.write_text(
            "name,phone\n"
            "Suryansh,+919876543210\n"
            "Mumbai Bakers,+919812345678\n")
        print(f"created {CONTACTS.name} — put real numbers in it")

    if not STOCK.exists():
        STOCK.write_text(
            "product,qty,price,note\n"
            "Bread (per loaf),120,22,fresh this morning\n"
            "Mangoes (per kg),80,60,Alphonso\n"
            "Cooking Oil (1L),45,140,\n")
        print(f"created {STOCK.name} — put real stock in it")


# =============================================================================
# DATA
# =============================================================================

def normalise(phone: str) -> str:
    """'98765 43210' -> '+919876543210'. Leaves a full +… number alone."""
    raw = re.sub(r"[^\d+]", "", str(phone).strip())
    if raw.startswith("+"):
        return raw
    if len(raw) == 10:                       # bare Indian mobile
        return DEFAULT_COUNTRY + raw
    if raw.startswith("91") and len(raw) == 12:
        return "+" + raw
    return "+" + raw


def read_contacts() -> list[dict]:
    with CONTACTS.open() as f:
        rows = [r for r in csv.DictReader(f) if (r.get("phone") or "").strip()]
    for r in rows:
        r["phone"] = normalise(r["phone"])
        r["name"] = (r.get("name") or "there").strip()
    return rows


def read_stock() -> list[dict]:
    with STOCK.open() as f:
        return [r for r in csv.DictReader(f) if (r.get("product") or "").strip()]


def compose(name: str, stock: list[dict]) -> str:
    """The message itself. Edit this freely — it is the whole point."""
    lines = [f"Hi {name}! 👋", "", "*New stock in today:*", ""]
    for item in stock:
        bit = f"• {item['product']} — {item['qty']} available"
        if (item.get("price") or "").strip():
            bit += f" @ ₹{item['price']}"
        if (item.get("note") or "").strip():
            bit += f"  _{item['note']}_"
        lines.append(bit)
    lines += ["", "Reply with what you need and we'll hold it for you.",
              "— WKC Store"]
    return "\n".join(lines)


# =============================================================================
# SENDING
# =============================================================================

def make_client(sid: str, token: str):
    try:
        from twilio.rest import Client
    except ImportError:
        sys.exit("pip install twilio")
    return Client(sid, token)


def send(client, sender: str, to: str, body: str, image: str | None,
         content_sid: str | None = None, content_vars: dict | None = None):
    kwargs = {"from_": sender, "to": "whatsapp:" + to}

    if content_sid:
        # Trial accounts cannot send freeform text — only approved templates.
        kwargs["content_sid"] = content_sid
        if content_vars:
            kwargs["content_variables"] = json.dumps(content_vars)
    else:
        kwargs["body"] = body
        if image:
            # Twilio fetches this itself, so it has to be public.
            kwargs["media_url"] = [image]

    return client.messages.create(**kwargs)


def list_templates(sid: str, token: str) -> None:
    """Every approved template on this account, with its ContentSid."""
    client = make_client(sid, token)
    try:
        rows = client.content.v1.contents.list(limit=50)
    except Exception as exc:
        sys.exit("Could not read templates: " + explain(exc) +
                 "\n\nBuild one in the console instead:\n"
                 "  Messaging -> Content Template Builder -> Create new\n"
                 "Then copy its ContentSid (starts with HX).")

    if not rows:
        print("No templates on this account yet.\n"
              "Make one at Console -> Messaging -> Content Template Builder,\n"
              "then run this again to get its ContentSid.")
        return

    print(f"{len(rows)} template(s):")
    for c in rows:
        print(f"\n  {c.friendly_name}")
        print(f"    ContentSid : {c.sid}")
        print(f"    language   : {c.language}")
        if c.variables:
            print(f"    variables  : {', '.join(sorted(c.variables))}")
        for kind, spec in (c.types or {}).items():
            text = (spec or {}).get("body")
            if text:
                print(f"    [{kind}] {text[:160]}")


def log(row: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["at", "name", "phone", "sid", "status", "error"])
        if new:
            w.writeheader()
        w.writerow(row)


def parse_vars(pairs: list[str] | None) -> dict:
    """--var 1=Bread --var 2=120  ->  {"1": "Bread", "2": "120"}"""
    out = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        out[key.strip()] = value
    return out


def explain(exc: Exception) -> str:
    """Twilio's own errors carry the useful bit in .code and .msg."""
    code = getattr(exc, "code", None)
    hints = {
        63015: "recipient has not joined your sandbox (send 'join <code>' first)",
        63016: "outside the 24h window — they must message you again, or use a template",
        21211: "the number is not valid E.164",
        63007: "the 'from' number is not a WhatsApp sender on this account",
        21654: ("this trial account cannot send freeform text — use a template.\n"
                "          Run: python whatsapp_blast.py templates\n"
                "          Then add: --content-sid HX... --var 1=... --var 2=..."),
    }
    text = getattr(exc, "msg", None) or str(exc)
    if code in hints:
        return f"[{code}] {hints[code]}"
    return f"[{code}] {text}" if code else text


# =============================================================================
# COMMANDS
# =============================================================================

def cmd_test(args) -> None:
    sid, token, sender = credentials()
    client = make_client(sid, token)
    to = normalise(args.phone)
    body = args.text or "Test from the WKC stock bot ✅"
    print(f"-> {to}")
    try:
        m = send(client, sender, to, body, args.image,
                 args.content_sid, parse_vars(args.var))
        print(f"   sent. sid={m.sid} status={m.status}")
    except Exception as exc:
        sys.exit("   failed: " + explain(exc))


def cmd_blast(args) -> None:
    seed_files()
    contacts, stock = read_contacts(), read_stock()
    if not contacts:
        sys.exit("contacts.csv is empty")
    if not stock:
        sys.exit("stock.csv is empty")

    print(f"{len(contacts)} contacts · {len(stock)} products"
          + ("  (dry run)" if args.dry_run else ""))

    if args.dry_run:
        print("\n" + "-" * 52)
        print(compose(contacts[0]["name"], stock))
        print("-" * 52)
        print("\nWould send that to:")
        for c in contacts:
            print(f"  {c['name']:<22} {c['phone']}")
        return

    sid, token, sender = credentials()
    client = make_client(sid, token)
    ok = fail = 0

    for i, c in enumerate(contacts, 1):
        body = compose(c["name"], stock)
        print(f"[{i}/{len(contacts)}] {c['name']:<22} {c['phone']}", end=" ")
        try:
            m = send(client, sender, c["phone"], body, args.image,
                     args.content_sid, parse_vars(args.var))
            print(f"sent ({m.status})")
            log({"at": datetime.now().isoformat(timespec="seconds"),
                 "name": c["name"], "phone": c["phone"],
                 "sid": m.sid, "status": m.status, "error": ""})
            ok += 1
        except Exception as exc:
            reason = explain(exc)
            print("FAILED " + reason)
            log({"at": datetime.now().isoformat(timespec="seconds"),
                 "name": c["name"], "phone": c["phone"],
                 "sid": "", "status": "failed", "error": reason})
            fail += 1

        if i < len(contacts):
            time.sleep(THROTTLE_SECONDS)     # sandbox: 1 message / 3 seconds

    print(f"\n{ok} sent, {fail} failed. Details in {LOG.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="WhatsApp stock alerts via Twilio")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("templates", help="list approved templates and their ContentSid")

    t = sub.add_parser("test", help="send one message to one number")
    t.add_argument("phone")
    t.add_argument("text", nargs="?", default=None)
    t.add_argument("--image", default=None)
    t.add_argument("--content-sid", dest="content_sid", default=None)
    t.add_argument("--var", action="append", default=[], metavar="N=VALUE")

    b = sub.add_parser("blast", help="send the stock list to everyone in contacts.csv")
    b.add_argument("--image", default=None, help="public https URL of a photo")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--content-sid", dest="content_sid", default=None)
    b.add_argument("--var", action="append", default=[], metavar="N=VALUE")

    args = ap.parse_args()

    if args.cmd == "templates":
        sid, token, _ = credentials()
        list_templates(sid, token)
        return

    {"test": cmd_test, "blast": cmd_blast}[args.cmd](args)


if __name__ == "__main__":
    main()
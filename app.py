#!/usr/bin/env python3
"""
TripMint — Single-file Python web app.

Features:
- Real accounts with password hashing
- Session cookies
- SQLite database
- Itinerary planner + saved itineraries
- Products/guides for sale
- Paystack-ready checkout
- Embedded frontend
- Contact + launch list
- Runs with Python standard library only

Run:
    python app.py

Environment variables:
    PORT                  default 8000
    BASE_URL              public URL, e.g. https://yourdomain.com
    PAYSTACK_SECRET_KEY   set tomorrow for real Paystack verification
    PAYSTACK_PUBLIC_KEY   optional, exposed to frontend config
"""

import json
import os
import re
import sqlite3
import secrets
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

DB_PATH = os.environ.get("TRIPMINT_DB", "tripmint.db")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
BASE_URL = os.environ.get("BASE_URL") or f"http://localhost:{PORT}"

PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS itineraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            destination TEXT NOT NULL,
            route_title TEXT NOT NULL,
            priority TEXT,
            pace TEXT,
            constraint_text TEXT,
            travel_date TEXT,
            data TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS products (
            handle TEXT PRIMARY KEY,
            destination_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            price_minor INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'NGN',
            active INTEGER NOT NULL DEFAULT 1,
            guide_content TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_handle TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            reference TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            verified_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(product_handle) REFERENCES products(handle)
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS launch_subscribers (
            email TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'research',
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_package_id INTEGER NOT NULL,
            category TEXT,
            claim TEXT NOT NULL,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'unverified',
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_package_id) REFERENCES source_packages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS guide_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_package_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            version INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL,
            content TEXT,
            source_snapshot TEXT,
            review_questions TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_package_id) REFERENCES source_packages(id) ON DELETE CASCADE
        );
    """)

    seed_products(conn)
    seed_research_demo(conn)

    conn.commit()
    conn.close()


def seed_products(conn):
    products = [
        {
            "handle": "rome-one-day-first-timer-decision-guide",
            "destination_key": "rome",
            "title": "Rome: One-Day First-Timer Decision Guide",
            "description": "Choose the right Rome route, protect timed entry, and know what to cut.",
            "price_minor": 450000,
            "currency": "NGN",
            "guide_content": {
                "notice": "Unlocked guide. Confirm opening hours, tickets, transport, and weather with official sources before travel.",
                "routes": [
                    {
                        "title": "First-timer classic",
                        "protects": "the major ancient-core anchors",
                        "gives_up": "long neighbourhood lingering",
                        "blocks": [
                            "Anchor the morning to your timed Colosseum or Forum entry.",
                            "Keep late morning for the walkable ancient core.",
                            "Move lunch away from the monument perimeter.",
                            "Close with a historic-centre walk and viewpoint."
                        ]
                    },
                    {
                        "title": "Slow food and neighbourhood",
                        "protects": "food, pauses, and one major sight",
                        "gives_up": "multiple landmark interiors",
                        "blocks": [
                            "Start with one major sight and keep it short.",
                            "Use mid-morning for a market or café stop.",
                            "Make lunch the main event.",
                            "End with a walkable neighbourhood loop."
                        ]
                    }
                ],
                "recovery": [
                    "If timed entry fails, use the ancient core from outside and save interiors for another slot.",
                    "If heat is high, move museum time to midday and keep evening for walking.",
                    "If transport is disrupted, stay in one district instead of crossing the city."
                ],
                "checklist": [
                    "Confirm opening hours and last entry.",
                    "Check timed-entry availability.",
                    "Save offline maps and transport alternatives.",
                    "Choose one graceful cut before you leave."
                ]
            }
        },
        {
            "handle": "florence-art-day-decision-guide",
            "destination_key": "florence",
            "title": "Florence: Art Day Decision Guide",
            "description": "Pick one major art anchor and protect slow movement around it.",
            "price_minor": 400000,
            "currency": "NGN",
            "guide_content": {
                "notice": "Unlocked guide. Confirm museum reservations and access rules before travel.",
                "routes": [
                    {
                        "title": "Art and cathedral core",
                        "protects": "one reserved museum and the cathedral area",
                        "gives_up": "rushing multiple museums",
                        "blocks": [
                            "Start near the cathedral before crowds.",
                            "Use mid-morning for one reserved museum.",
                            "Take lunch away from the tightest tourist core.",
                            "Finish with a bridge-and-viewpoint loop."
                        ]
                    }
                ],
                "recovery": [
                    "If dome access is limited, swap it for a riverside walk.",
                    "If museums sell out, use façades, plazas, and market areas."
                ],
                "checklist": [
                    "Confirm reservation window.",
                    "Keep one indoor anchor.",
                    "Protect a slow food stop."
                ]
            }
        },
        {
            "handle": "paris-family-day-decision-guide",
            "destination_key": "paris",
            "title": "Paris: Family Day Decision Guide",
            "description": "Keep Paris simple, calm, and memorable for families with limited energy.",
            "price_minor": 420000,
            "currency": "NGN",
            "guide_content": {
                "notice": "Unlocked guide. Check museum family access, weather, and transport before travel.",
                "routes": [
                    {
                        "title": "Low-stress family route",
                        "protects": "simple movement and open space",
                        "gives_up": "long queues",
                        "blocks": [
                            "Choose one large sight and one open space.",
                            "Keep food simple and familiar.",
                            "Use a short walk or ride instead of long transfers.",
                            "End early with a treat before fatigue."
                        ]
                    }
                ],
                "recovery": [
                    "If rain arrives, choose one museum and one covered route.",
                    "If energy drops, cut one indoor stop and protect food and rest."
                ],
                "checklist": [
                    "Confirm family-friendly entry.",
                    "Save offline metro map.",
                    "Plan one rest stop."
                ]
            }
        }
    ]

    for p in products:
        conn.execute(
            """
            INSERT OR IGNORE INTO products
            (handle, destination_key, title, description, price_minor, currency, active, guide_content)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                p["handle"],
                p["destination_key"],
                p["title"],
                p["description"],
                p["price_minor"],
                p["currency"],
                json.dumps(p["guide_content"], ensure_ascii=False),
            ),
        )


def seed_research_demo(conn):
    """
    Demonstrates the source-package/claims/guide-draft data model.
    This is stored in the database even though the public UI focuses on selling guides.
    """
    exists = conn.execute("SELECT COUNT(*) AS c FROM source_packages").fetchone()["c"]
    if exists:
        return

    conn.execute(
        """
        INSERT INTO source_packages (destination, title, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "Rome",
            "Rome one-day first-timer evidence",
            "approved",
            "Demo research package for the Rome guide.",
            now_iso(),
        ),
    )

    package_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    claims = [
        ("timing", "Timed entry reduces queue risk for major Roman landmarks.", "https://example.com/rome-timed-entry", "verified"),
        ("route", "The ancient core is walkable for first-time visitors.", "https://example.com/rome-route", "verified"),
        ("food", "Lunch value improves away from monument perimeter.", "https://example.com/rome-food", "verified"),
    ]

    for category, claim, source_url, status in claims:
        conn.execute(
            """
            INSERT INTO claims (source_package_id, category, claim, source_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (package_id, category, claim, source_url, status, now_iso()),
        )

    conn.execute(
        """
        INSERT INTO guide_drafts
        (source_package_id, status, version, title, content, source_snapshot, review_questions, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            package_id,
            "draft",
            1,
            "Rome guide draft",
            json.dumps({"message": "Draft generation should be connected to your LLM workflow."}, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps(["Verify opening hours.", "Verify timed entry.", "Verify transport alternatives."], ensure_ascii=False),
            None,
            now_iso(),
        ),
    )


# ------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------

def get_secret():
    conn = get_db()
    row = conn.execute("SELECT value FROM meta WHERE key = 'secret'").fetchone()
    if row:
        secret = row["value"]
    else:
        secret = secrets.token_hex(32)
        conn.execute("INSERT INTO meta (key, value) VALUES ('secret', ?)", (secret,))
        conn.commit()
    conn.close()
    return secret


SECRET = None


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()


def create_user(name, email, password):
    conn = get_db()
    user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    is_admin = 1 if user_count == 0 else 0

    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)

    cur = conn.execute(
        """
        INSERT INTO users (name, email, password_hash, salt, is_admin, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name.strip(), email.strip().lower(), password_hash, salt, is_admin, now_iso()),
    )
    user_id = cur.lastrowid
    conn.commit()

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def verify_user(email, password):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    conn.close()

    if not user:
        return None

    expected = user["password_hash"]
    actual = hash_password(password, user["salt"])

    if not secrets.compare_digest(expected, actual):
        return None

    return user


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=30)

    conn = get_db()
    conn.execute(
        """
        INSERT INTO sessions (token, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, user_id, now_iso(), expires.isoformat()),
    )
    conn.commit()
    conn.close()

    return token


def delete_session(token):
    if not token:
        return
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def get_user_from_token(token):
    if not token:
        return None

    conn = get_db()
    row = conn.execute(
        """
        SELECT u.*
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
        """,
        (token, now_iso()),
    ).fetchone()
    conn.close()
    return row


def session_cookie(token):
    return "app_session={}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000".format(token)


def clear_session_cookie():
    return "app_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def user_to_dict(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
    }


# ------------------------------------------------------------------
# Paystack helpers
# ------------------------------------------------------------------

def paystack_request(path, payload=None, method="POST"):
    url = "https://api.paystack.co" + path
    headers = {
        "Authorization": "Bearer " + PAYSTACK_SECRET_KEY,
        "Content-Type": "application/json",
    }

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def paystack_initialize(email, amount_minor, reference):
    payload = {
        "email": email,
        "amount": int(amount_minor),
        "reference": reference,
        "callback_url": BASE_URL.rstrip("/") + "/api/paystack/callback",
    }
    return paystack_request("/transaction/initialize", payload, method="POST")


def paystack_verify(reference):
    result = paystack_request("/transaction/verify/" + urllib.parse.quote(reference), method="GET")
    return bool(result.get("status")) and result.get("data", {}).get("status") == "success"


# ------------------------------------------------------------------
# Data helpers
# ------------------------------------------------------------------

def row_to_itinerary(row):
    item = dict(row)
    try:
        item["data"] = json.loads(item.get("data") or "{}")
    except Exception:
        item["data"] = {}
    return item


def row_to_product(row, purchased=False):
    item = dict(row)
    item["price"] = item["price_minor"] / 100
    item["purchased"] = purchased

    if purchased:
        try:
            item["guide_content"] = json.loads(item.get("guide_content") or "{}")
        except Exception:
            item["guide_content"] = {}
    else:
        item.pop("guide_content", None)

    return item


def get_purchased_handles(user_id):
    if not user_id:
        return set()

    conn = get_db()
    rows = conn.execute(
        """
        SELECT product_handle
        FROM orders
        WHERE user_id = ? AND status IN ('success', 'verified', 'demo')
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    return {row["product_handle"] for row in rows}


# ------------------------------------------------------------------
# Embedded frontend
# ------------------------------------------------------------------

APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TripMint — Travel Decisions, Simplified</title>
  <style>
    :root {
      --bg: #061d26;
      --panel: #082831;
      --panel-2: #06222c;
      --text: #f6f1e7;
      --muted: rgba(246,241,231,.68);
      --accent: #58ded7;
      --orange: #f07945;
      --line: rgba(255,255,255,.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background:
        radial-gradient(circle at top left, rgba(88,222,215,.12), transparent 28%),
        radial-gradient(circle at top right, rgba(240,121,69,.12), transparent 20%),
        var(--bg);
      color: var(--text);
      line-height: 1.55;
    }
    body.no-scroll { overflow: hidden; }
    a { color: inherit; text-decoration: none; }
    button, input, select, textarea { font: inherit; }
    .container { width: min(1120px, calc(100% - 32px)); margin: 0 auto; }
    .site-header {
      position: sticky; top: 0; z-index: 50;
      backdrop-filter: blur(14px);
      background: rgba(6,29,38,.82);
      border-bottom: 1px solid var(--line);
    }
    .nav { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 14px 0; }
    .brand { font-weight: 800; font-size: 18px; cursor: pointer; }
    .nav-links { display: flex; gap: 8px; flex-wrap: wrap; }
    .nav-links button {
      background: none; border: none; color: rgba(246,241,231,.75);
      cursor: pointer; padding: 8px 10px; border-radius: 10px;
    }
    .nav-links button:hover { color: var(--text); background: rgba(255,255,255,.05); }
    .btn {
      display: inline-flex; align-items: center; justify-content: center;
      min-height: 42px; padding: 11px 16px; border-radius: 12px;
      border: none; background: var(--accent); color: #062028;
      font-weight: 800; cursor: pointer;
    }
    .btn:disabled { opacity: .6; cursor: not-allowed; }
    .btn-ghost { background: transparent; color: var(--text); border: 1px solid rgba(255,255,255,.18); }
    .btn-danger { background: rgba(240,121,69,.14); color: #ffb08c; border: 1px solid rgba(240,121,69,.35); }
    .full { width: 100%; }
    .hero { padding: 72px 0 34px; }
    .hero-grid { display: grid; gap: 28px; grid-template-columns: 1.15fr .85fr; align-items: center; }
    .badge {
      display: inline-flex; padding: 7px 11px; border-radius: 999px;
      border: 1px solid rgba(88,222,215,.35); color: var(--accent);
      font-size: 12px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase;
    }
    h1 { margin: 16px 0 12px; font-size: clamp(34px,6vw,68px); line-height: .98; }
    .lead { margin: 0; color: var(--muted); font-size: 17px; max-width: 680px; }
    .hero-actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; }
    .hero-trust { display: grid; gap: 8px; margin-top: 22px; color: var(--muted); font-size: 14px; }
    .panel {
      background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.015));
      border: 1px solid var(--line); border-radius: 24px; padding: 22px;
      box-shadow: 0 20px 60px rgba(0,0,0,.22);
    }
    .section { padding: 56px 0; }
    .section-alt { background: rgba(255,255,255,.02); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
    .section-head { max-width: 760px; margin-bottom: 26px; }
    .section-head h2 { margin: 0 0 8px; font-size: clamp(28px,4vw,42px); line-height: 1.02; }
    .section-head p { margin: 0; color: var(--muted); }
    .grid-2 { display: grid; grid-template-columns: 1.05fr .95fr; gap: 22px; align-items: start; }
    label { display: block; font-size: 13px; font-weight: 700; margin: 13px 0 6px; color: rgba(246,241,231,.84); }
    input, select, textarea {
      width: 100%; background: #041720; color: var(--text);
      border: 1px solid rgba(255,255,255,.14); border-radius: 12px;
      padding: 11px 12px; outline: none;
    }
    textarea { min-height: 120px; resize: vertical; }
    .muted { color: var(--muted); }
    .small { font-size: 13px; }
    .hidden { display: none !important; }
    .form-error { color: #ff9c7a; min-height: 18px; font-size: 13px; margin: 9px 0; }
    .timeline { margin: 0; padding-left: 18px; display: grid; gap: 12px; }
    .time { display: inline-flex; margin-bottom: 4px; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
    .timeline p { margin: 0; color: rgba(246,241,231,.88); }
    .result-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px; }
    .result-cols h4 { margin: 0 0 8px; }
    .result-cols ul { margin: 0; padding-left: 18px; color: var(--muted); }
    .result-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }
    .saved-card {
      display: flex; align-items: center; justify-content: space-between; gap: 14px;
      padding: 13px 14px; border: 1px solid rgba(255,255,255,.1);
      border-radius: 16px; margin-bottom: 10px; background: rgba(255,255,255,.02);
    }
    .saved-card h4 { margin: 0 0 4px; font-size: 15px; }
    .saved-card p { margin: 0; color: var(--muted); font-size: 13px; }
    .saved-actions { display: flex; gap: 8px; }
    .saved-actions button {
      background: rgba(255,255,255,.06); color: var(--text);
      border: 1px solid rgba(255,255,255,.12); border-radius: 10px;
      padding: 8px 10px; cursor: pointer;
    }
    .product-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
    .product-card {
      border: 1px solid var(--line); border-radius: 24px; padding: 20px;
      display: flex; flex-direction: column; gap: 12px;
      background: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.018));
    }
    .product-tag {
      align-self: flex-start; font-size: 11px; font-weight: 800;
      letter-spacing: .12em; text-transform: uppercase; color: var(--orange);
      border: 1px solid rgba(240,121,69,.35); padding: 6px 8px; border-radius: 999px;
    }
    .product-card h3 { margin: 0; font-size: 22px; line-height: 1.08; }
    .product-desc { margin: 0; color: var(--muted); }
    .product-footer { margin-top: auto; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 8px; }
    .price { font-weight: 800; font-size: 18px; }
    .trust-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
    .trust-card { border: 1px solid var(--line); border-radius: 22px; padding: 18px; background: rgba(255,255,255,.02); }
    .trust-card h3 { margin: 0 0 8px; font-size: 17px; }
    .trust-card p { margin: 0; color: var(--muted); font-size: 14px; }
    .narrow { max-width: 720px; }
    .site-footer { border-top: 1px solid var(--line); padding: 22px 0; }
    .footer-row { display: flex; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
    .modal { position: fixed; inset: 0; display: none; z-index: 1000; }
    .modal.open { display: block; }
    .modal-backdrop { position: absolute; inset: 0; background: rgba(2,10,14,.72); backdrop-filter: blur(3px); }
    .modal-card {
      position: relative; width: min(680px, calc(100vw - 32px)); margin: 7vh auto;
      background: #07222c; border: 1px solid var(--line); border-radius: 26px;
      padding: 22px; max-height: 86vh; overflow: auto;
    }
    .modal-close {
      position: absolute; top: 14px; right: 14px; width: 38px; height: 38px;
      border-radius: 999px; border: 1px solid rgba(255,255,255,.14);
      background: rgba(255,255,255,.05); color: var(--text); cursor: pointer;
      font-size: 20px; line-height: 1;
    }
    .tabs { display: flex; gap: 8px; margin: 14px 0; }
    .tab {
      flex: 1; padding: 10px 12px; border-radius: 12px;
      border: 1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.03); color: var(--text); cursor: pointer; font-weight: 700;
    }
    .tab.active { background: rgba(88,222,215,.12); border-color: rgba(88,222,215,.38); color: var(--accent); }
    .auth-signed { display: flex; align-items: center; gap: 10px; }
    .avatar {
      width: 34px; height: 34px; border-radius: 999px; display: inline-flex;
      align-items: center; justify-content: center; background: rgba(88,222,215,.16);
      color: var(--accent); font-weight: 800; border: 1px solid rgba(88,222,215,.28);
    }
    .auth-name { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; }
    .checkout-summary {
      border: 1px solid rgba(255,255,255,.12); background: rgba(255,255,255,.03);
      border-radius: 18px; padding: 14px; margin-bottom: 14px;
    }
    .checkout-summary h3 { margin: 0 0 6px; }
    .modal-actions { display: flex; justify-content: space-between; gap: 12px; margin-top: 18px; }
    .guide-notice {
      padding: 12px 14px; border-radius: 14px; background: rgba(88,222,215,.08);
      border: 1px solid rgba(88,222,215,.18); color: rgba(246,241,231,.86);
      margin: 12px 0; font-size: 14px;
    }
    .guide-route {
      border: 1px solid rgba(255,255,255,.1); border-radius: 18px;
      padding: 14px; margin-bottom: 12px; background: rgba(255,255,255,.02);
    }
    .guide-route h4 { margin: 0 0 8px; }
    .guide-route p { margin: 6px 0; color: var(--muted); }
    .guide-route ul { margin: 8px 0 0; padding-left: 18px; color: var(--muted); }
    .toast {
      position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%) translateY(20px);
      opacity: 0; pointer-events: none; background: #0b3140; color: var(--text);
      border: 1px solid var(--line); padding: 12px 16px; border-radius: 12px;
      transition: .25s ease; z-index: 2000; max-width: min(560px, calc(100vw - 32px));
      text-align: center;
    }
    .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    .toast.error { border-color: rgba(240,121,69,.55); }
    @media (max-width: 980px) {
      .hero-grid, .grid-2 { grid-template-columns: 1fr; }
      .product-grid, .trust-grid { grid-template-columns: 1fr; }
      .result-cols { grid-template-columns: 1fr; }
      .nav { align-items: flex-start; }
    }
  </style>
</head>
<body>
  <header class="site-header">
    <div class="container nav">
      <div class="brand" data-scroll="top">TripMint</div>
      <nav class="nav-links">
        <button data-scroll="planner">Planner</button>
        <button data-scroll="guides">Guides</button>
        <button data-scroll="trust">Trust</button>
        <button data-scroll="contact">Contact</button>
      </nav>
      <div id="authArea"></div>
    </div>
  </header>

  <main>
    <section class="hero" id="top">
      <div class="container hero-grid">
        <div>
          <span class="badge">Travel decisions, simplified</span>
          <h1>Build a one-day itinerary that actually works.</h1>
          <p class="lead">
            TripMint helps you choose the right route, protect the most important booking,
            and know exactly what to cut when time is limited.
          </p>
          <div class="hero-actions">
            <button class="btn" data-scroll="planner">Create itinerary</button>
            <button class="btn btn-ghost" data-scroll="guides">Buy decision guides</button>
          </div>
          <div class="hero-trust">
            <div>• Real accounts and cloud-saved itineraries</div>
            <div>• Practical route logic instead of overloaded lists</div>
            <div>• Paystack-ready checkout for guide sales</div>
          </div>
        </div>
        <div class="panel">
          <h2 style="margin-top:0;">Launch checklist</h2>
          <p class="muted">Join the list for early access to new destination guides.</p>
          <form id="launchForm">
            <label for="launchEmail">Email address</label>
            <input id="launchEmail" type="email" placeholder="you@example.com" required />
            <div style="margin-top:14px;">
              <button class="btn full" type="submit">Join the launch list</button>
            </div>
          </form>
        </div>
      </div>
    </section>

    <section id="planner" class="section">
      <div class="container">
        <div class="section-head">
          <h2>One-day itinerary planner</h2>
          <p>Generate a clear route, save it to your account, and reopen it anytime.</p>
        </div>
        <div class="grid-2">
          <form id="planForm" class="panel">
            <label for="destination">Destination</label>
            <select id="destination">
              <option value="rome">Rome</option>
              <option value="florence">Florence</option>
              <option value="paris">Paris</option>
              <option value="lisbon">Lisbon</option>
              <option value="custom">Custom destination</option>
            </select>

            <div id="customDestinationWrap" class="hidden">
              <label for="customDestination">Custom destination name</label>
              <input id="customDestination" placeholder="Example: Accra" />
            </div>

            <label for="planDate">Travel date</label>
            <input id="planDate" type="date" />

            <label for="priority">Priority</label>
            <select id="priority">
              <option value="firsttime">First-time essentials</option>
              <option value="culture">Culture and landmarks</option>
              <option value="food">Food and local flavour</option>
              <option value="family">Easy family pace</option>
            </select>

            <label for="pace">Pace</label>
            <select id="pace">
              <option value="balanced">Balanced</option>
              <option value="slow">Slow</option>
              <option value="packed">Packed</option>
            </select>

            <label for="constraint">Main constraint</label>
            <select id="constraint">
              <option value="none">None</option>
              <option value="mobility">Mobility or stairs</option>
              <option value="heat">Heat or sun exposure</option>
              <option value="rain">Rain backup</option>
              <option value="budget">Budget sensitivity</option>
            </select>

            <div style="margin-top:18px;">
              <button class="btn full" type="submit">Generate itinerary</button>
            </div>
          </form>

          <div>
            <div id="itineraryOutput" class="panel hidden"></div>
            <div class="panel" style="margin-top:18px;">
              <h3 style="margin-top:0;">Saved itineraries</h3>
              <div id="savedList"></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section id="guides" class="section section-alt">
      <div class="container">
        <div class="section-head">
          <h2>Decision guides</h2>
          <p>Practical guides for travellers who want a clear day instead of endless research.</p>
        </div>
        <div id="productGrid" class="product-grid"></div>
      </div>
    </section>

    <section id="trust" class="section">
      <div class="container">
        <div class="section-head">
          <h2>Trust signals</h2>
          <p>Clear promises, no hidden booking claims, and honest travel guidance.</p>
        </div>
        <div class="trust-grid">
          <div class="trust-card"><h3>Real accounts</h3><p>Itineraries and purchases are tied to your secure account.</p></div>
          <div class="trust-card"><h3>No hidden booking role</h3><p>TripMint sells planning guides. It is not a travel agent.</p></div>
          <div class="trust-card"><h3>Official source prompts</h3><p>Travellers are reminded to confirm hours, tickets, transport, and access.</p></div>
          <div class="trust-card"><h3>Paystack-ready</h3><p>Checkout and verification are structured for Paystack integration.</p></div>
        </div>
      </div>
    </section>

    <section id="contact" class="section section-alt">
      <div class="container">
        <div class="section-head">
          <h2>Contact</h2>
          <p>Ask a question, request a destination, or get help before purchase.</p>
        </div>
        <form id="contactForm" class="panel narrow">
          <label for="contactName">Name</label>
          <input id="contactName" required />
          <label for="contactEmail">Email</label>
          <input id="contactEmail" type="email" required />
          <label for="contactSubject">Subject</label>
          <input id="contactSubject" required />
          <label for="contactMessage">Message</label>
          <textarea id="contactMessage" required></textarea>
          <div style="margin-top:16px;">
            <button class="btn" type="submit">Send message</button>
          </div>
        </form>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="container footer-row">
      <div>© <span id="year"></span> TripMint. A day that works.</div>
      <div class="muted small">Python single-file build.</div>
    </div>
  </footer>

  <div class="modal" id="authModal" aria-hidden="true">
    <div class="modal-backdrop" data-close></div>
    <div class="modal-card">
      <button class="modal-close" data-close aria-label="Close">×</button>
      <h2 style="margin-top:0;">Sign in to TripMint</h2>
      <div class="tabs">
        <button type="button" id="authTabSignIn" class="tab active">Sign in</button>
        <button type="button" id="authTabSignUp" class="tab">Create account</button>
      </div>

      <form id="signinForm">
        <label for="signinEmail">Email</label>
        <input id="signinEmail" type="email" required />
        <label for="signinPassword">Password</label>
        <input id="signinPassword" type="password" required />
        <div id="signinError" class="form-error"></div>
        <button class="btn full" type="submit">Sign in</button>
      </form>

      <form id="signupForm" class="hidden">
        <label for="signupName">Name</label>
        <input id="signupName" required />
        <label for="signupEmail">Email</label>
        <input id="signupEmail" type="email" required />
        <label for="signupPassword">Password</label>
        <input id="signupPassword" type="password" minlength="8" required />
        <div id="signupError" class="form-error"></div>
        <button class="btn full" type="submit">Create account</button>
      </form>
    </div>
  </div>

  <div class="modal" id="checkoutModal" aria-hidden="true">
    <div class="modal-backdrop" data-close></div>
    <div class="modal-card">
      <button class="modal-close" data-close aria-label="Close">×</button>
      <h2 style="margin-top:0;">Checkout</h2>
      <div class="checkout-summary">
        <h3 id="checkoutTitle"></h3>
        <div id="checkoutPrice" class="price"></div>
      </div>
      <p class="muted small" id="checkoutNotice"></p>
      <div class="modal-actions">
        <button id="checkoutCancelButton" class="btn btn-ghost" type="button">Cancel</button>
        <button id="checkoutButton" class="btn" type="button">Pay and unlock</button>
      </div>
    </div>
  </div>

  <div class="modal" id="guideModal" aria-hidden="true">
    <div class="modal-backdrop" data-close></div>
    <div class="modal-card">
      <button class="modal-close" data-close aria-label="Close">×</button>
      <h2 id="guideTitle" style="margin-top:0;"></h2>
      <div id="guideBody"></div>
    </div>
  </div>

  <div id="toast" class="toast" role="status"></div>

  <script>
    const state = {
      user: null,
      itineraries: [],
      products: [],
      currentItinerary: null,
      checkoutProduct: null
    };

    const $ = (id) => document.getElementById(id);

    function escapeHtml(value) {
      const div = document.createElement('div');
      div.textContent = value == null ? '' : String(value);
      return div.innerHTML;
    }

    let toastTimer;
    function toast(message, isError = false) {
      const el = $('toast');
      el.textContent = message;
      el.className = 'toast show' + (isError ? ' error' : '');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { el.className = 'toast'; }, 4200);
    }

    function formatPrice(product) {
      return (product.currency || 'NGN') + ' ' + Number(product.price || 0).toLocaleString();
    }

    async function api(path, options = {}) {
      const opts = {
        method: options.method || 'GET',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: options.body
      };
      const res = await fetch(path, opts);
      let data = {};
      try { data = await res.json(); } catch (e) {}
      if (!res.ok) throw new Error(data.error || ('Request failed with status ' + res.status));
      return data;
    }

    function openModal(id) {
      closeAllModals();
      $(id).classList.add('open');
      document.body.classList.add('no-scroll');
    }

    function closeAllModals() {
      document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
      document.body.classList.remove('no-scroll');
    }

    function scrollToId(id) {
      const el = document.getElementById(id);
      if (!el) return;
      if (id === 'top') window.scrollTo({ top: 0, behavior: 'smooth' });
      else el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    async function loadUser() {
      const data = await api('/api/me');
      state.user = data.user;
      renderAuthArea();
    }

    async function loadItineraries() {
      const data = await api('/api/itineraries');
      state.itineraries = data.itineraries || [];
      renderSavedItineraries();
    }

    async function loadProducts() {
      const data = await api('/api/products');
      state.products = data.products || [];
      renderProducts();
    }

    async function refreshAll() {
      await Promise.all([loadItineraries(), loadProducts()]);
    }

    function renderAuthArea() {
      const el = $('authArea');
      if (state.user) {
        const initial = (state.user.name || state.user.email || 'U').charAt(0).toUpperCase();
        el.innerHTML = `
          <div class="auth-signed">
            <span class="avatar">${escapeHtml(initial)}</span>
            <span class="auth-name">${escapeHtml(state.user.name)}</span>
            <button class="btn btn-ghost" id="signOutBtn">Sign out</button>
          </div>
        `;
        $('signOutBtn').onclick = signOut;
      } else {
        el.innerHTML = `<button class="btn btn-ghost" id="openAuthBtn">Sign in</button>`;
        $('openAuthBtn').onclick = () => openModal('authModal');
      }
    }

    function switchAuthTab(tab) {
      const isSignIn = tab === 'signin';
      $('signinForm').classList.toggle('hidden', !isSignIn);
      $('signupForm').classList.toggle('hidden', isSignIn);
      $('authTabSignIn').classList.toggle('active', isSignIn);
      $('authTabSignUp').classList.toggle('active', !isSignIn);
    }

    async function signOut() {
      try {
        await api('/api/auth/logout', { method: 'POST' });
        state.user = null;
        await refreshAll();
        renderAuthArea();
        toast('Signed out.');
      } catch (err) {
        toast(err.message, true);
      }
    }

    const DESTINATIONS = {
      rome: {
        label: 'Rome',
        routes: [
          {
            title: 'First-timer classic',
            blocks: [
              'Anchor the morning to your timed Colosseum or Forum entry.',
              'Keep late morning for the walkable ancient core.',
              'Move lunch away from the monument perimeter.',
              'Close with a historic-centre walk and viewpoint.'
            ]
          },
          {
            title: 'Slow food and neighbourhood',
            blocks: [
              'Start with one major sight and keep it short.',
              'Use mid-morning for a market or café stop.',
              'Make lunch the main event.',
              'End with a walkable neighbourhood loop.'
            ]
          }
        ],
        recovery: [
          'If timed entry fails, use the ancient core from outside.',
          'If heat is high, move indoor during midday.',
          'If transport is disrupted, stay in one district.'
        ]
      },
      florence: {
        label: 'Florence',
        routes: [
          {
            title: 'Art and cathedral core',
            blocks: [
              'Start near the cathedral before crowds.',
              'Use mid-morning for one reserved museum.',
              'Take lunch away from the tightest tourist core.',
              'Finish with a bridge-and-viewpoint loop.'
            ]
          }
        ],
        recovery: [
          'If dome access is limited, swap for a riverside walk.',
          'If museums sell out, use façades and plazas.'
        ]
      },
      paris: {
        label: 'Paris',
        routes: [
          {
            title: 'Low-stress family route',
            blocks: [
              'Choose one large sight and one open space.',
              'Keep food simple and familiar.',
              'Use a short walk or ride instead of long transfers.',
              'End early with a treat before fatigue.'
            ]
          }
        ],
        recovery: [
          'If rain arrives, choose one museum and one covered route.',
          'If energy drops, cut one indoor stop.'
        ]
      },
      lisbon: {
        label: 'Lisbon',
        routes: [
          {
            title: 'Historic hills',
            blocks: [
              'Start in one historic district.',
              'Use mid-morning for a market stop.',
              'Take lunch away from the main tourist lane.',
              'Finish with one viewpoint.'
            ]
          }
        ],
        recovery: [
          'If hills are too much, use lifts or one flat district.',
          'If wind appears, choose museums and covered cafés.'
        ]
      }
    };

    function customPlan(name) {
      return {
        title: 'Custom day in ' + name,
        blocks: [
          'Choose one anchor activity and build the morning around it.',
          'Keep midday flexible with food and rest nearby.',
          'Use the afternoon for one district instead of multiple attractions.',
          'End with a viewpoint, meal, or calm walk.'
        ],
        recovery: [
          'If weather changes, move indoor during midday.',
          'If energy drops, cut one stop and protect food and rest.'
        ]
      };
    }

    function generateItinerary() {
      const destinationKey = $('destination').value;
      const priority = $('priority').value;
      const pace = $('pace').value;
      const constraint = $('constraint').value;
      const travelDate = $('planDate').value;

      let destinationName;
      let route;

      if (destinationKey === 'custom') {
        destinationName = $('customDestination').value.trim() || 'your destination';
        route = customPlan(destinationName);
      } else {
        const destination = DESTINATIONS[destinationKey];
        destinationName = destination.label;
        route = destination.routes[0];
      }

      const labels = ['Morning', 'Midday', 'Afternoon', 'Evening'];
      const maxItems = pace === 'slow' ? 3 : 4;
      const items = route.blocks.slice(0, maxItems).map((text, index) => ({
        label: labels[index],
        text
      }));

      if (pace === 'packed') {
        items.push({ label: 'Optional extension', text: 'Add one low-friction stop near dinner if energy remains.' });
      }

      const cuts = [
        'Keep one anchor and protect it.',
        'Cut the stop that requires the longest queue.',
        'Keep dinner simple if the day becomes uncertain.'
      ];

      if (constraint === 'mobility') cuts.push('Choose one district and remove stairs, hills, and long transfers.');
      if (constraint === 'heat') cuts.push('Move indoor during midday and keep evening light.');
      if (constraint === 'rain') cuts.push('Swap open-air routes for one museum and one covered food stop.');
      if (constraint === 'budget') cuts.push('Prioritise free viewpoints, streets, and markets over paid interiors.');

      state.currentItinerary = {
        destination: destinationName,
        route_title: route.title,
        priority,
        pace,
        constraint_text: constraint,
        travel_date: travelDate,
        data: {
          summary: `A ${pace} ${route.title.toLowerCase()} for ${destinationName}.`,
          items,
          cuts,
          recovery: route.recovery || []
        }
      };

      renderCurrentItinerary();
    }

    function renderCurrentItinerary() {
      const it = state.currentItinerary;
      const el = $('itineraryOutput');
      const data = it.data || {};

      el.classList.remove('hidden');
      el.innerHTML = `
        <h3>${escapeHtml(it.route_title)}</h3>
        <p class="muted">${escapeHtml(data.summary || '')}</p>
        <ol class="timeline">
          ${(data.items || []).map(item => `
            <li>
              <span class="time">${escapeHtml(item.label)}</span>
              <p>${escapeHtml(item.text)}</p>
            </li>
          `).join('')}
        </ol>
        <div class="result-cols">
          <div>
            <h4>Graceful cuts</h4>
            <ul>${(data.cuts || []).map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>
          </div>
          <div>
            <h4>If this happens</h4>
            <ul>${(data.recovery || []).map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
          </div>
        </div>
        <div class="result-actions">
          <button class="btn" id="saveItineraryBtn">Save to account</button>
        </div>
      `;

      $('saveItineraryBtn').onclick = saveCurrentItinerary;
    }

    async function saveCurrentItinerary() {
      if (!state.user) {
        openModal('authModal');
        toast('Sign in to save itineraries.', true);
        return;
      }

      const btn = $('saveItineraryBtn');
      btn.disabled = true;

      try {
        await api('/api/itineraries', {
          method: 'POST',
          body: JSON.stringify(state.currentItinerary)
        });
        await loadItineraries();
        toast('Itinerary saved.');
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = false;
      }
    }

    function renderSavedItineraries() {
      const el = $('savedList');

      if (!state.user) {
        el.innerHTML = `<p class="muted">Sign in to save and reopen your itineraries.</p>`;
        return;
      }

      if (!state.itineraries.length) {
        el.innerHTML = `<p class="muted">No saved itineraries yet.</p>`;
        return;
      }

      el.innerHTML = state.itineraries.map(item => `
        <article class="saved-card">
          <div>
            <h4>${escapeHtml(item.route_title)}</h4>
            <p>${escapeHtml(item.destination)} • ${escapeHtml(item.pace || 'balanced')} pace</p>
          </div>
          <div class="saved-actions">
            <button data-action="load" data-id="${item.id}">Open</button>
            <button data-action="delete" data-id="${item.id}">Delete</button>
          </div>
        </article>
      `).join('');
    }

    function renderProducts() {
      const el = $('productGrid');

      el.innerHTML = state.products.map(product => `
        <article class="product-card">
          <div class="product-tag">Decision guide</div>
          <h3>${escapeHtml(product.title)}</h3>
          <p class="product-desc">${escapeHtml(product.description || '')}</p>
          <div class="product-footer">
            <div class="price">${escapeHtml(formatPrice(product))}</div>
            ${product.purchased
              ? `<button class="btn btn-ghost" data-action="open-guide" data-handle="${escapeHtml(product.handle)}">Open guide</button>`
              : `<button class="btn" data-action="buy" data-handle="${escapeHtml(product.handle)}">Buy guide</button>`
            }
          </div>
        </article>
      `).join('');
    }

    function openCheckout(handle) {
      if (!state.user) {
        openModal('authModal');
        toast('Sign in before checkout.', true);
        return;
      }

      const product = state.products.find(p => p.handle === handle);
      if (!product) return;

      state.checkoutProduct = product;
      $('checkoutTitle').textContent = product.title;
      $('checkoutPrice').textContent = formatPrice(product);
      $('checkoutNotice').textContent = 'If Paystack keys are not configured yet, this creates a demo unlock. Add Paystack keys to take real payments.';
      openModal('checkoutModal');
    }

    async function processCheckout() {
      const btn = $('checkoutButton');
      btn.disabled = true;

      try {
        const res = await api('/api/checkout', {
          method: 'POST',
          body: JSON.stringify({ product_handle: state.checkoutProduct.handle })
        });

        if (res.status === 'redirect' && res.authorization_url) {
          window.location.href = res.authorization_url;
          return;
        }

        await loadProducts();
        closeAllModals();
        toast('Guide unlocked.');
        openGuide(state.checkoutProduct.handle);
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = false;
      }
    }

    function openGuide(handle) {
      const product = state.products.find(p => p.handle === handle);
      if (!product || !product.purchased) {
        toast('Purchase required.', true);
        return;
      }

      const guide = product.guide_content || {};
      $('guideTitle').textContent = product.title;

      let html = `<div class="guide-notice">${escapeHtml(guide.notice || 'Unlocked guide.')}</div>`;

      if (Array.isArray(guide.routes)) {
        html += `<h3>Routes</h3>`;
        html += guide.routes.map(route => `
          <div class="guide-route">
            <h4>${escapeHtml(route.title)}</h4>
            <p><strong>Protects:</strong> ${escapeHtml(route.protects || 'the main plan')}.</p>
            <p><strong>Gives up:</strong> ${escapeHtml(route.gives_up || 'optional extras')}.</p>
            <ul>
              ${(route.blocks || []).map(b => `<li>${escapeHtml(b)}</li>`).join('')}
            </ul>
          </div>
        `).join('');
      }

      if (Array.isArray(guide.recovery)) {
        html += `<h3>Recovery branches</h3><ul>${guide.recovery.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`;
      }

      if (Array.isArray(guide.checklist)) {
        html += `<h3>Before you go</h3><ul>${guide.checklist.map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>`;
      }

      $('guideBody').innerHTML = html;
      openModal('guideModal');
    }

    function bindEvents() {
      $('year').textContent = new Date().getFullYear();

      document.querySelectorAll('[data-scroll]').forEach(btn => {
        btn.addEventListener('click', () => scrollToId(btn.dataset.scroll));
      });

      document.addEventListener('click', (e) => {
        if (e.target.matches('[data-close]')) closeAllModals();
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeAllModals();
      });

      $('authTabSignIn').onclick = () => switchAuthTab('signin');
      $('authTabSignUp').onclick = () => switchAuthTab('signup');

      $('signinForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        $('signinError').textContent = '';
        try {
          await api('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify({
              email: $('signinEmail').value,
              password: $('signinPassword').value
            })
          });
          await loadUser();
          await refreshAll();
          closeAllModals();
          toast('Signed in.');
        } catch (err) {
          $('signinError').textContent = err.message;
        }
      });

      $('signupForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        $('signupError').textContent = '';
        try {
          await api('/api/auth/signup', {
            method: 'POST',
            body: JSON.stringify({
              name: $('signupName').value,
              email: $('signupEmail').value,
              password: $('signupPassword').value
            })
          });
          await loadUser();
          await refreshAll();
          closeAllModals();
          toast('Account created.');
        } catch (err) {
          $('signupError').textContent = err.message;
        }
      });

      $('planForm').addEventListener('submit', (e) => {
        e.preventDefault();
        generateItinerary();
        scrollToId('itineraryOutput');
      });

      $('destination').addEventListener('change', (e) => {
        $('customDestinationWrap').classList.toggle('hidden', e.target.value !== 'custom');
      });

      $('savedList').addEventListener('click', async (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;

        const id = Number(btn.dataset.id);
        const action = btn.dataset.action;

        if (action === 'load') {
          const item = state.itineraries.find(x => x.id === id);
          if (item) {
            state.currentItinerary = item;
            renderCurrentItinerary();
            scrollToId('itineraryOutput');
          }
        }

        if (action === 'delete') {
          if (!window.confirm('Delete this itinerary?')) return;
          try {
            await api('/api/itineraries/' + id, { method: 'DELETE' });
            await loadItineraries();
            toast('Itinerary deleted.');
          } catch (err) {
            toast(err.message, true);
          }
        }
      });

      $('productGrid').addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;

        const handle = btn.dataset.handle;
        if (btn.dataset.action === 'buy') openCheckout(handle);
        if (btn.dataset.action === 'open-guide') openGuide(handle);
      });

      $('checkoutButton').onclick = processCheckout;
      $('checkoutCancelButton').onclick = closeAllModals;

      $('launchForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
          await api('/api/launch', {
            method: 'POST',
            body: JSON.stringify({ email: $('launchEmail').value })
          });
          e.target.reset();
          toast('You are on the launch list.');
        } catch (err) {
          toast(err.message, true);
        }
      });

      $('contactForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
          await api('/api/contact', {
            method: 'POST',
            body: JSON.stringify({
              name: $('contactName').value,
              email: $('contactEmail').value,
              subject: $('contactSubject').value,
              message: $('contactMessage').value
            })
          });
          e.target.reset();
          toast('Message sent.');
        } catch (err) {
          toast(err.message, true);
        }
      });
    }

    async function init() {
      bindEvents();

      const params = new URLSearchParams(window.location.search);
      if (params.get('purchased')) toast('Payment verified. Guide unlocked.');
      if (params.get('payment') === 'failed') toast('Payment failed or was cancelled.', true);

      try {
        await loadUser();
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    init();
  </script>
</body>
</html>
"""


# ------------------------------------------------------------------
# HTTP handler
# ------------------------------------------------------------------

class TripMintHandler(BaseHTTPRequestHandler):
    server_version = "TripMint/1.0"

    # ---------------- utilities ----------------

    def send_json(self, data, status=200, set_cookie=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        if name not in cookie:
            return None
        return cookie[name].value

    def current_user(self):
        token = self.get_cookie("app_session")
        return get_user_from_token(token)

    def require_user(self):
        user = self.current_user()
        if not user:
            self.send_json({"error": "Authentication required."}, 401)
            return None
        return user

    # ---------------- GET ----------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self.send_html(APP_HTML)

        if path == "/health":
            return self.send_json({"ok": True, "service": "TripMint"})

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/api/config":
            return self.send_json({
                "paystackPublicKey": PAYSTACK_PUBLIC_KEY,
                "paymentsEnabled": bool(PAYSTACK_SECRET_KEY),
                "baseUrl": BASE_URL,
            })

        if path == "/api/me":
            user = self.current_user()
            return self.send_json({"user": user_to_dict(user)})

        if path == "/api/itineraries":
            user = self.current_user()
            if not user:
                return self.send_json({"itineraries": []})

            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM itineraries WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
            conn.close()

            return self.send_json({"itineraries": [row_to_itinerary(row) for row in rows]})

        if path == "/api/products":
            user = self.current_user()
            purchased = get_purchased_handles(user["id"] if user else None)

            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM products WHERE active = 1 ORDER BY price_minor ASC"
            ).fetchall()
            conn.close()

            products = [row_to_product(row, row["handle"] in purchased) for row in rows]
            return self.send_json({"products": products})

        if path == "/api/orders":
            user = self.require_user()
            if not user:
                return

            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
            conn.close()

            return self.send_json({"orders": [dict(row) for row in rows]})

        if path == "/api/paystack/callback":
            query = urllib.parse.parse_qs(parsed.query)
            reference = (query.get("reference") or [None])[0]

            if not reference:
                return self.redirect("/?payment=failed")

            if not PAYSTACK_SECRET_KEY:
                return self.redirect("/?payment=failed")

            try:
                ok = paystack_verify(reference)
            except Exception:
                return self.redirect("/?payment=failed")

            if not ok:
                return self.redirect("/?payment=failed")

            conn = get_db()
            order = conn.execute("SELECT * FROM orders WHERE reference = ?", (reference,)).fetchone()
            if not order:
                conn.close()
                return self.redirect("/?payment=failed")

            conn.execute(
                "UPDATE orders SET status = 'success', verified_at = ? WHERE reference = ?",
                (now_iso(), reference),
            )
            conn.commit()
            product_handle = order["product_handle"]
            conn.close()

            return self.redirect("/?purchased=" + urllib.parse.quote(product_handle))

        return self.send_json({"error": "Not found."}, 404)

    # ---------------- POST ----------------

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/auth/signup":
            body = self.read_json()
            name = str(body.get("name") or "").strip()
            email = str(body.get("email") or "").strip().lower()
            password = str(body.get("password") or "")

            if len(name) < 2:
                return self.send_json({"error": "Name is too short."}, 400)
            if not EMAIL_RE.match(email):
                return self.send_json({"error": "Enter a valid email."}, 400)
            if len(password) < 8:
                return self.send_json({"error": "Password must be at least 8 characters."}, 400)

            conn = get_db()
            exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            conn.close()

            if exists:
                return self.send_json({"error": "An account with this email already exists."}, 409)

            try:
                user = create_user(name, email, password)
            except Exception:
                return self.send_json({"error": "Could not create account."}, 500)

            token = create_session(user["id"])
            return self.send_json({"user": user_to_dict(user)}, set_cookie=session_cookie(token))

        if path == "/api/auth/login":
            body = self.read_json()
            email = str(body.get("email") or "").strip().lower()
            password = str(body.get("password") or "")

            user = verify_user(email, password)
            if not user:
                return self.send_json({"error": "Invalid email or password."}, 401)

            token = create_session(user["id"])
            return self.send_json({"user": user_to_dict(user)}, set_cookie=session_cookie(token))

        if path == "/api/auth/logout":
            token = self.get_cookie("app_session")
            delete_session(token)
            return self.send_json({"ok": True}, set_cookie=clear_session_cookie())

        if path == "/api/itineraries":
            user = self.require_user()
            if not user:
                return

            body = self.read_json()
            destination = str(body.get("destination") or "").strip()
            route_title = str(body.get("route_title") or "").strip()
            priority = str(body.get("priority") or "").strip()
            pace = str(body.get("pace") or "").strip()
            constraint_text = str(body.get("constraint_text") or "").strip()
            travel_date = str(body.get("travel_date") or "").strip()
            data = body.get("data") or {}

            if not destination or not route_title:
                return self.send_json({"error": "Itinerary destination and route title are required."}, 400)

            conn = get_db()
            cur = conn.execute(
                """
                INSERT INTO itineraries
                (user_id, destination, route_title, priority, pace, constraint_text, travel_date, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    destination,
                    route_title,
                    priority,
                    pace,
                    constraint_text,
                    travel_date,
                    json.dumps(data, ensure_ascii=False),
                    now_iso(),
                ),
            )
            itinerary_id = cur.lastrowid
            row = conn.execute("SELECT * FROM itineraries WHERE id = ?", (itinerary_id,)).fetchone()
            conn.commit()
            conn.close()

            return self.send_json({"itinerary": row_to_itinerary(row)}, status=201)

        if path == "/api/contact":
            body = self.read_json()
            name = str(body.get("name") or "").strip()
            email = str(body.get("email") or "").strip().lower()
            subject = str(body.get("subject") or "").strip()
            message = str(body.get("message") or "").strip()

            if len(name) < 2:
                return self.send_json({"error": "Name is too short."}, 400)
            if not EMAIL_RE.match(email):
                return self.send_json({"error": "Enter a valid email."}, 400)
            if len(subject) < 3:
                return self.send_json({"error": "Subject is too short."}, 400)
            if len(message) < 10:
                return self.send_json({"error": "Message is too short."}, 400)

            user = self.current_user()

            conn = get_db()
            conn.execute(
                """
                INSERT INTO contacts (user_id, name, email, subject, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user["id"] if user else None, name, email, subject, message, now_iso()),
            )
            conn.commit()
            conn.close()

            return self.send_json({"ok": True})

        if path == "/api/launch":
            body = self.read_json()
            email = str(body.get("email") or "").strip().lower()

            if not EMAIL_RE.match(email):
                return self.send_json({"error": "Enter a valid email."}, 400)

            conn = get_db()
            conn.execute(
                "INSERT OR IGNORE INTO launch_subscribers (email, created_at) VALUES (?, ?)",
                (email, now_iso()),
            )
            conn.commit()
            conn.close()

            return self.send_json({"ok": True})

        if path == "/api/checkout":
            user = self.require_user()
            if not user:
                return

            body = self.read_json()
            product_handle = str(body.get("product_handle") or "").strip()

            conn = get_db()
            product = conn.execute(
                "SELECT * FROM products WHERE handle = ? AND active = 1",
                (product_handle,),
            ).fetchone()

            if not product:
                conn.close()
                return self.send_json({"error": "Product not found."}, 404)

            already = conn.execute(
                """
                SELECT id FROM orders
                WHERE user_id = ? AND product_handle = ? AND status IN ('success', 'verified', 'demo')
                """,
                (user["id"], product_handle),
            ).fetchone()

            if already:
                conn.close()
                return self.send_json({"status": "owned"})

            reference = "tm_" + secrets.token_hex(10)

            conn.execute(
                """
                INSERT INTO orders
                (user_id, product_handle, amount_minor, currency, reference, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    user["id"],
                    product_handle,
                    product["price_minor"],
                    product["currency"],
                    reference,
                    now_iso(),
                ),
            )
            conn.commit()

            if not PAYSTACK_SECRET_KEY:
                conn.execute(
                    "UPDATE orders SET status = 'demo', verified_at = ? WHERE reference = ?",
                    (now_iso(), reference),
                )
                conn.commit()
                conn.close()
                return self.send_json({"status": "demo", "reference": reference})

            conn.close()

            try:
                result = paystack_initialize(user["email"], product["price_minor"], reference)
                authorization_url = result.get("data", {}).get("authorization_url")
                if not authorization_url:
                    raise Exception("Paystack did not return an authorization URL.")
                return self.send_json({"status": "redirect", "authorization_url": authorization_url})
            except Exception as err:
                return self.send_json({"error": "Paystack initialization failed: " + str(err)}, 502)

        if path == "/api/paystack/verify":
            user = self.require_user()
            if not user:
                return

            body = self.read_json()
            reference = str(body.get("reference") or "").strip()
            if not reference:
                return self.send_json({"error": "Reference is required."}, 400)

            conn = get_db()
            order = conn.execute(
                "SELECT * FROM orders WHERE reference = ? AND user_id = ?",
                (reference, user["id"]),
            ).fetchone()

            if not order:
                conn.close()
                return self.send_json({"error": "Order not found."}, 404)

            if not PAYSTACK_SECRET_KEY:
                conn.execute(
                    "UPDATE orders SET status = 'demo', verified_at = ? WHERE reference = ?",
                    (now_iso(), reference),
                )
                conn.commit()
                conn.close()
                return self.send_json({"ok": True, "status": "demo"})

            try:
                ok = paystack_verify(reference)
            except Exception as err:
                conn.close()
                return self.send_json({"error": "Paystack verification failed: " + str(err)}, 502)

            if ok:
                conn.execute(
                    "UPDATE orders SET status = 'success', verified_at = ? WHERE reference = ?",
                    (now_iso(), reference),
                )
                conn.commit()
                conn.close()
                return self.send_json({"ok": True, "status": "success"})

            conn.close()
            return self.send_json({"error": "Payment was not successful."}, 402)

        return self.send_json({"error": "Not found."}, 404)

    # ---------------- DELETE ----------------

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        match = re.match(r"^/api/itineraries/(\d+)$", path)
        if match:
            user = self.require_user()
            if not user:
                return

            itinerary_id = int(match.group(1))
            conn = get_db()
            conn.execute(
                "DELETE FROM itineraries WHERE id = ? AND user_id = ?",
                (itinerary_id, user["id"]),
            )
            conn.commit()
            conn.close()
            return self.send_json({"ok": True})

        return self.send_json({"error": "Not found."}, 404)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    global SECRET
    init_db()
    SECRET = get_secret()

    server = ThreadingHTTPServer((HOST, PORT), TripMintHandler)
    print("=" * 60)
    print("TripMint Python app is running.")
    print(f"Local: http://localhost:{PORT}")
    print(f"Database file: {DB_PATH}")
    print("Paystack enabled:", bool(PAYSTACK_SECRET_KEY))
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()

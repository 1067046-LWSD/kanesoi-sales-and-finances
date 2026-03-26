"""
db_setup.py — Initialize and seed the SQLite database for Finance + Sales agents.
Run once: python db_setup.py
Creates finance_sales.db with pre-seeded data.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "finance_sales.db")


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # ── Finance tables ──────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT NOT NULL,
            quarter TEXT NOT NULL,
            allocated REAL NOT NULL,
            spent REAL NOT NULL DEFAULT 0,
            UNIQUE(department, quarter)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            vendor TEXT,
            amount REAL,
            status TEXT,       -- paid | pending | overdue
            due_date TEXT,
            department TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS revenue (
            deal_id TEXT PRIMARY KEY,
            company TEXT,
            amount REAL,
            closed_date TEXT,
            source TEXT DEFAULT 'SALES'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            amount REAL,
            month TEXT,        -- YYYY-MM
            department TEXT
        )
    """)

    # ── Sales tables ────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            company TEXT,
            contact TEXT,
            email TEXT,
            segment TEXT,
            size INTEGER,
            fit TEXT,          -- high | medium | low
            score INTEGER,
            stage TEXT         -- new | demo_scheduled | proposal_sent | closed_won | nurture
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            deal_id TEXT PRIMARY KEY,
            lead_id TEXT,
            company TEXT,
            amount REAL,
            stage TEXT,
            close_date TEXT,
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        )
    """)

    conn.commit()

    # ── Seed data ───────────────────────────────────────────────────
    c.executemany(
        "INSERT OR IGNORE INTO budgets (department, quarter, allocated, spent) VALUES (?,?,?,?)",
        [
            ("engineering", "Q2", 50000, 31200),
            ("marketing",   "Q2", 20000, 17500),
            ("sales",       "Q2", 30000, 14000),
            ("hr",          "Q2", 15000,  8900),
            ("engineering", "Q1", 48000, 47100),
            ("marketing",   "Q1", 18000, 19200),  # overspent — triggers RULE 1
            ("sales",       "Q1", 28000, 27500),
            ("hr",          "Q1", 14000, 13100),
        ],
    )

    c.executemany(
        "INSERT OR IGNORE INTO invoices (invoice_id, vendor, amount, status, due_date, department) VALUES (?,?,?,?,?,?)",
        [
            ("INV-001", "AWS",        4200,  "paid",    "2026-03-01", "engineering"),
            ("INV-002", "HubSpot",     900,  "pending", "2026-04-01", "sales"),
            ("INV-003", "Contractor", 7500,  "overdue", "2026-02-15", "engineering"),
            ("INV-004", "Figma",       300,  "paid",    "2026-03-10", "marketing"),
            ("INV-005", "Notion",      200,  "overdue", "2026-02-01", "hr"),
            ("INV-006", "GitHub",      840,  "pending", "2026-04-15", "engineering"),
        ],
    )

    c.executemany(
        "INSERT OR IGNORE INTO revenue (deal_id, company, amount, closed_date) VALUES (?,?,?,?)",
        [
            ("DEAL-101", "Maple Bakery",   1200,  "2026-01-10"),
            ("DEAL-102", "Nexus Corp",    45000,  "2026-02-15"),
            ("DEAL-103", "Urban Plumbing", 2800,  "2026-03-20"),
        ],
    )

    # Monthly expenses for cash flow simulator
    c.executemany(
        "INSERT OR IGNORE INTO expenses (category, amount, month, department) VALUES (?,?,?,?)",
        [
            ("salaries",    35000, "2026-01", "all"),
            ("infra",        4200, "2026-01", "engineering"),
            ("marketing",    3500, "2026-01", "marketing"),
            ("salaries",    35000, "2026-02", "all"),
            ("infra",        4200, "2026-02", "engineering"),
            ("marketing",    3800, "2026-02", "marketing"),
            ("salaries",    35000, "2026-03", "all"),
            ("infra",        4500, "2026-03", "engineering"),
            ("marketing",    4000, "2026-03", "marketing"),
        ],
    )

    c.executemany(
        "INSERT OR IGNORE INTO leads (id, company, contact, email, segment, size, fit, score, stage) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("L-001", "Maple Bakery",     "Sara Kim",    "sara@maplebakery.com",     "small_business", 5,   "high",   85, "new"),
            ("L-002", "Urban Plumbing",   "James Tran",  "james@urbanplumbing.com",  "small_business", 12,  "medium", 42, "new"),
            ("L-003", "Corner Bookshop",  "Priya Nair",  "priya@cornerbookshop.com", "small_business", 3,   "high",   78, "new"),
            ("L-004", "Nexus Corp",       "David Chen",  "david@nexuscorp.com",      "enterprise",     500, "high",   91, "demo_scheduled"),
            ("L-005", "Vertex Solutions", "Amy Wallace", "amy@vertexsolutions.com",  "enterprise",     200, "medium", 55, "new"),
            ("L-006", "Bright Cafe",      "Tom Russo",   "tom@brightcafe.com",       "small_business", 8,   "low",    30, "new"),
            ("L-007", "Orbit Analytics",  "Lin Zhao",    "lin@orbitanalytics.com",   "enterprise",     350, "high",   88, "proposal_sent"),
        ],
    )

    c.executemany(
        "INSERT OR IGNORE INTO deals (deal_id, lead_id, company, amount, stage, close_date) VALUES (?,?,?,?,?,?)",
        [
            ("DEAL-101", "L-001", "Maple Bakery",   1200,  "closed_won", "2026-01-10"),
            ("DEAL-102", "L-004", "Nexus Corp",    45000,  "closed_won", "2026-02-15"),
            ("DEAL-103", "L-002", "Urban Plumbing", 2800,  "closed_won", "2026-03-20"),
        ],
    )

    conn.commit()
    conn.close()
    print(f"✅ Database initialized at: {DB_PATH}")


if __name__ == "__main__":
    init_db()

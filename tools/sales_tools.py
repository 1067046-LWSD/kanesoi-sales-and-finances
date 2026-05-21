"""
tools/sales_tools.py — Real tools for the Sales Agent.
Week 6 deliverable: CRM sim, lead qualifier, pitch generator, pipeline tracker.
"""

import sqlite3
import json
import os
import random
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "sales.db")

# Pipeline stages in order
PIPELINE_STAGES = ["prospect", "qualified", "demo", "proposal", "negotiation", "closed_won", "closed_lost"]

# Scoring weights for lead qualification
QUALIFICATION_WEIGHTS = {
    "company_size": {"enterprise": 40, "mid_market": 25, "smb": 10},
    "budget_confirmed": {True: 30, False: 0},
    "timeline": {"immediate": 20, "q1": 15, "q2": 10, "q3": 5, "unknown": 0},
    "decision_maker": {True: 10, False: 0},
}


# ─── Database setup ────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id              TEXT PRIMARY KEY,
            company         TEXT NOT NULL,
            contact_name    TEXT,
            contact_email   TEXT,
            segment         TEXT,  -- enterprise | mid_market | smb
            product         TEXT,
            target_deal_usd REAL,
            stage           TEXT DEFAULT 'prospect',
            score           INTEGER DEFAULT 0,
            budget_confirmed INTEGER DEFAULT 0,
            timeline        TEXT DEFAULT 'unknown',
            decision_maker  INTEGER DEFAULT 0,
            notes           TEXT,
            created_at      TEXT,
            updated_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS deals (
            id              TEXT PRIMARY KEY,
            lead_id         TEXT,
            company         TEXT,
            deal_value_usd  REAL,
            stage           TEXT,
            closed_at       TEXT,
            contract_url    TEXT
        );

        CREATE TABLE IF NOT EXISTS pitches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     TEXT,
            pitch_text  TEXT,
            generated_at TEXT
        );
    """)
    conn.commit()

    # Seed sample leads
    sample_leads = [
        ("lead-001", "AcmeCorp", "Sarah Chen", "sarah@acme.com", "enterprise", "SaaS-Pro", 48000, "prospect", "q1", 1, 1),
        ("lead-002", "StartupXYZ", "Mike Torres", "mike@startupxyz.com", "smb", "SaaS-Starter", 6000, "prospect", "q2", 0, 0),
        ("lead-003", "MidCo Industries", "Janet Kim", "janet@midco.com", "mid_market", "SaaS-Business", 18000, "qualified", "immediate", 1, 1),
        ("lead-004", "GlobalTech", "Raj Patel", "raj@globaltech.com", "enterprise", "SaaS-Enterprise", 72000, "demo", "q1", 1, 1),
        ("lead-005", "LocalBiz", "Emma Ross", "emma@localbiz.com", "smb", "SaaS-Starter", 3600, "prospect", "unknown", 0, 0),
    ]
    now = datetime.now(timezone.utc).isoformat()
    for lead in sample_leads:
        c.execute("""INSERT OR IGNORE INTO leads
            (id, company, contact_name, contact_email, segment, product, target_deal_usd, stage, timeline, budget_confirmed, decision_maker, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*lead, now, now))
    conn.commit()
    conn.close()


# ─── Tool 1: Lead Qualifier ────────────────────────────────────────────────────

def qualify_lead(lead_id: str) -> dict:
    """Score and qualify a lead using BANT-style criteria."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"Lead {lead_id} not found"}

    cols = [d[0] for d in c.description]
    lead = dict(zip(cols, row))
    conn.close()

    # Scoring
    score = 0
    score += QUALIFICATION_WEIGHTS["company_size"].get(lead.get("segment", ""), 0)
    score += QUALIFICATION_WEIGHTS["budget_confirmed"].get(bool(lead.get("budget_confirmed")), 0)
    score += QUALIFICATION_WEIGHTS["timeline"].get(lead.get("timeline", "unknown"), 0)
    score += QUALIFICATION_WEIGHTS["decision_maker"].get(bool(lead.get("decision_maker")), 0)

    qualified = score >= 50
    new_stage = "qualified" if qualified and lead["stage"] == "prospect" else lead["stage"]

    # Update score + stage
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE leads SET score=?, stage=?, updated_at=? WHERE id=?",
              (score, new_stage, datetime.now(timezone.utc).isoformat(), lead_id))
    conn.commit()
    conn.close()

    return {
        "lead_id": lead_id,
        "company": lead["company"],
        "segment": lead["segment"],
        "score": score,
        "qualified": qualified,
        "stage": new_stage,
        "target_deal_usd": lead.get("target_deal_usd"),
        "recommendation": "Proceed to demo" if score >= 70 else ("Nurture further" if score >= 40 else "Deprioritize"),
    }


# ─── Tool 2: Pitch Generator (template-based; LLM call in agent) ──────────────

PITCH_TEMPLATES = {
    "enterprise": """Hi {contact_name},

I'm reaching out because companies like {company} are facing growing pressure to {pain_point}.
Our {product} is purpose-built for enterprise teams — {key_benefit}.

We've helped similar organizations achieve {outcome} within the first quarter.

Given your scale and the immediacy of your timeline, I'd love to schedule a personalized demo.
Would 30 minutes this week work?

Best,
[Sales Agent]
Deal value: ${deal_value:,}""",

    "mid_market": """Hi {contact_name},

{company} sounds like a great fit for {product} — mid-market teams consistently tell us that
{pain_point} is holding back growth. We solve that with {key_benefit}.

Typical results: {outcome}. Implementation takes under 2 weeks.

Want to see a quick demo? I can tailor it to your specific workflow.

Best,
[Sales Agent]
Deal value: ${deal_value:,}""",

    "smb": """Hi {contact_name},

Running {company} means wearing a lot of hats. {product} is designed to give small teams
the tools of a larger operation — {key_benefit} — without the enterprise price tag.

Many SMB customers see {outcome} in the first 30 days.

Happy to walk you through it in 15 minutes. When works best?

[Sales Agent]
Deal value: ${deal_value:,}""",
}

PAIN_POINTS = {
    "SaaS-Pro": "streamline team collaboration and reduce tool sprawl",
    "SaaS-Starter": "get started quickly without a big IT overhead",
    "SaaS-Business": "scale operations without proportional headcount growth",
    "SaaS-Enterprise": "unify data across departments and improve decision velocity",
}

KEY_BENEFITS = {
    "enterprise": "a unified platform with enterprise-grade security, SSO, and dedicated support",
    "mid_market": "easy integration with your existing stack and a 99.9% uptime SLA",
    "smb": "zero setup cost and an intuitive interface your team can learn in an hour",
}

OUTCOMES = {
    "enterprise": "40% reduction in cross-team communication overhead",
    "mid_market": "25% faster project delivery and 20% cost savings",
    "smb": "save 5+ hours per week per employee",
}


def generate_pitch(lead_id: str) -> dict:
    """Generate a personalized pitch for a lead."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"Lead {lead_id} not found"}
    cols = [d[0] for d in c.description]
    lead = dict(zip(cols, row))
    conn.close()

    segment = lead.get("segment", "smb")
    product = lead.get("product", "SaaS-Pro")
    template = PITCH_TEMPLATES.get(segment, PITCH_TEMPLATES["smb"])

    pitch = template.format(
        contact_name=lead.get("contact_name", "there"),
        company=lead.get("company", "your company"),
        product=product,
        pain_point=PAIN_POINTS.get(product, "grow efficiently"),
        key_benefit=KEY_BENEFITS.get(segment, "great value"),
        outcome=OUTCOMES.get(segment, "real results"),
        deal_value=int(lead.get("target_deal_usd", 0)),
    )

    # Persist pitch
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ts = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO pitches (lead_id, pitch_text, generated_at) VALUES (?,?,?)", (lead_id, pitch, ts))
    conn.commit()
    conn.close()

    return {
        "lead_id": lead_id,
        "company": lead["company"],
        "segment": segment,
        "pitch": pitch,
        "generated_at": ts,
    }


# ─── Tool 3: Close Deal ────────────────────────────────────────────────────────

def close_deal(lead_id: str, final_value_usd: float, won: bool = True) -> dict:
    """Mark a deal as closed (won or lost). Sends revenue to Finance."""
    import uuid as _uuid
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT company FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"Lead {lead_id} not found"}
    company = row[0]
    stage = "closed_won" if won else "closed_lost"
    ts = datetime.now(timezone.utc).isoformat()
    deal_id = f"deal-{_uuid.uuid4().hex[:8]}"
    c.execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?", (stage, ts, lead_id))
    if won:
        c.execute("INSERT INTO deals (id, lead_id, company, deal_value_usd, stage, closed_at) VALUES (?,?,?,?,?,?)",
                  (deal_id, lead_id, company, final_value_usd, stage, ts))
    conn.commit()
    conn.close()
    result = {"deal_id": deal_id, "lead_id": lead_id, "company": company,
              "stage": stage, "deal_value_usd": final_value_usd, "closed_at": ts}
    return result


# ─── Tool 4: Pipeline Report ───────────────────────────────────────────────────

def get_pipeline_report() -> dict:
    """Return full pipeline summary: stage counts, total value, conversion rates."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    stage_rows = c.execute("SELECT stage, COUNT(*), SUM(target_deal_usd) FROM leads GROUP BY stage").fetchall()
    total_revenue = c.execute("SELECT SUM(deal_value_usd) FROM deals WHERE stage='closed_won'").fetchone()[0] or 0
    total_deals = c.execute("SELECT COUNT(*) FROM deals WHERE stage='closed_won'").fetchone()[0] or 0
    avg_deal = (total_revenue / total_deals) if total_deals > 0 else 0
    conn.close()

    pipeline = {}
    total_pipeline_value = 0
    for stage, count, value in stage_rows:
        pipeline[stage] = {"count": count, "total_value_usd": round(value or 0, 2)}
        total_pipeline_value += value or 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_by_stage": pipeline,
        "total_pipeline_value_usd": round(total_pipeline_value, 2),
        "closed_won_revenue_usd": round(total_revenue, 2),
        "closed_deals": total_deals,
        "avg_deal_size_usd": round(avg_deal, 2),
    }


# ─── Tool 5: Upsell Identifier ────────────────────────────────────────────────

def identify_upsell_opportunities() -> list[dict]:
    """Find closed-won customers that could be upsold to a higher tier."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute("""
        SELECT l.id, l.company, l.product, l.target_deal_usd, l.segment
        FROM leads l JOIN deals d ON l.id = d.lead_id
        WHERE d.stage='closed_won' AND l.product != 'SaaS-Enterprise'
    """).fetchall()
    conn.close()

    upgrade_map = {
        "SaaS-Starter": ("SaaS-Business", 1.8),
        "SaaS-Business": ("SaaS-Pro", 1.5),
        "SaaS-Pro": ("SaaS-Enterprise", 1.6),
    }

    opportunities = []
    for lead_id, company, product, value, segment in rows:
        if product in upgrade_map:
            next_tier, multiplier = upgrade_map[product]
            opportunities.append({
                "lead_id": lead_id,
                "company": company,
                "current_product": product,
                "upsell_to": next_tier,
                "current_deal_usd": value,
                "upsell_value_usd": round(value * multiplier, 2),
            })

    return opportunities


# Initialize DB on import
init_db()

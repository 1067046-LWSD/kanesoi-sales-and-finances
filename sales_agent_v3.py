"""
Sales Agent — Week 6 (Final)
Framework: CrewAI-style role/task architecture

Tools (per design doc):
  1. lead_scorer          — score and qualify leads from SQLite
  2. message_writer       — Groq LLM personalized outreach (falls back to template)
  3. deal_tracker         — query/update deals in SQLite leads + deals tables
  4. notify_finance       — send closed deal info to Finance agent
  5. objection_handler    — match objection to prepared response; escalate if unknown

Decision Rules:
  RULE 1: Leads with score < 50 → skip, move to nurture
  RULE 2: Auto-pitch only high-fit leads with LLM; others get template
"""

import json
import logging
import os
import uuid
from datetime import date
from db_setup import get_conn, init_db, DB_PATH
from message_schema import AgentMessage
from finance_agent_v3 import FinanceAgent

# Ensure DB exists
if not os.path.exists(DB_PATH):
    init_db()

try:
    from groq import Groq
    _groq = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    GROQ_AVAILABLE = bool(os.environ.get("GROQ_API_KEY"))
except ImportError:
    _groq = None
    GROQ_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SALES] %(message)s")
log = logging.getLogger("sales")

QUALIFY_THRESHOLD = 50


# ══════════════════════════════════════════════════════════════════
#  TOOL 1: Lead Scorer
# ══════════════════════════════════════════════════════════════════

def lead_scorer(lead_id: str = "", segment: str = "") -> dict:
    """
    Score a single lead (by id) or list all leads for a segment.
    Applies RULE 1: score < 50 → not qualified.
    """
    conn = get_conn()
    if lead_id:
        row = conn.execute(
            "SELECT id, company, contact, email, segment, size, fit, score, stage FROM leads WHERE id=?",
            (lead_id,),
        ).fetchone()
        conn.close()
        if not row:
            return {"error": f"Lead {lead_id} not found"}
        lead = dict(zip(["id","company","contact","email","segment","size","fit","score","stage"], row))
        score = int(lead["score"])
        qualified = score >= QUALIFY_THRESHOLD
        lead["qualified"] = qualified
        lead["next_action"] = "Schedule demo" if qualified else f"Score {score} < {QUALIFY_THRESHOLD} — add to nurture"
        lead["rule_applied"] = None if qualified else "QUALIFY_THRESHOLD"
        log.info(f"lead_scorer({lead_id}) → score={score}, qualified={qualified}")
        return lead

    else:
        # List all leads, optionally filtered by segment
        query = "SELECT id, company, contact, fit, score, stage FROM leads"
        params = ()
        if segment:
            query += " WHERE segment=?"
            params = (segment,)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        leads = []
        for r in rows:
            l = dict(zip(["id","company","contact","fit","score","stage"], r))
            l["qualified"] = int(l["score"]) >= QUALIFY_THRESHOLD
            leads.append(l)
        log.info(f"lead_scorer(segment={segment}) → {len(leads)} leads")
        return {"leads": leads, "count": len(leads), "segment": segment or "all"}


# ══════════════════════════════════════════════════════════════════
#  TOOL 2: Message Writer (LLM-powered)
# ══════════════════════════════════════════════════════════════════

def message_writer(company: str, contact: str = "", pain_point: str = "efficiency",
                   fit: str = "medium") -> dict:
    """
    Write a personalized outreach email.
    RULE 2: Only uses Groq LLM for high-fit leads. Others get a template.
    """
    # RULE 2
    if fit != "high":
        log.info(f"RULE 2: {company} is '{fit}' fit — template pitch only")
        body = (f"Hi {contact or company}, we'd love to show you how our platform "
                f"helps companies like {company} with {pain_point}. "
                f"Would a quick 15-min call work this week?")
        return {
            "company": company, "contact": contact,
            "subject": f"Quick question for {company}",
            "body": body,
            "cta": "Book a 15-min call",
            "generated_by": "template",
            "rule_applied": "HIGH_FIT_ONLY",
        }

    # High-fit: use Groq if available
    if GROQ_AVAILABLE and _groq:
        try:
            prompt = (
                f"Write a short B2B sales email for '{company}' (contact: {contact}). "
                f"Pain point: '{pain_point}'. Under 80 words, conversational tone, "
                f"end with a demo CTA. Output ONLY the email body."
            )
            resp = _groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
            )
            body = resp.choices[0].message.content.strip()
            generated_by = "groq-llama3.3-70b"
            log.info(f"message_writer({company}) → LLM pitch generated")
        except Exception as e:
            log.warning(f"Groq error ({e}) — falling back to template")
            body = f"Hi {contact or company}, our platform solves {pain_point} for companies like {company}. Want a quick demo?"
            generated_by = "template-fallback"
    else:
        templates = {
            "efficiency": f"Hi {contact or company}, teams like {company} cut manual work by 40% with us. Worth a look?",
            "growth":     f"Hi {contact or company}, we help companies like {company} scale without the usual growing pains.",
            "cost":       f"Hi {contact or company}, our customers at {company}-sized companies save ~$8K/year. Happy to show you how.",
        }
        body = templates.get(pain_point, templates["efficiency"])
        generated_by = "template"
        log.info(f"message_writer({company}) → template (no Groq key)")

    return {
        "company": company, "contact": contact,
        "subject": f"Quick question for {company}",
        "body": body,
        "cta": "Would you have 15 minutes this week for a quick demo?",
        "generated_by": generated_by,
    }


# ══════════════════════════════════════════════════════════════════
#  TOOL 3: Deal Tracker
# ══════════════════════════════════════════════════════════════════

def deal_tracker(action: str, lead_id: str = "", stage: str = "",
                 amount: float = 0, deal_id: str = "") -> dict:
    """
    action='pipeline'  → return all open deals
    action='update'    → update a lead's stage
    action='close'     → mark deal closed_won and log to deals table
    """
    conn = get_conn()

    if action == "pipeline":
        rows = conn.execute(
            "SELECT id, company, contact, fit, score, stage FROM leads WHERE stage != 'closed_won'"
        ).fetchall()
        conn.close()
        pipeline = [dict(zip(["id","company","contact","fit","score","stage"], r)) for r in rows]
        log.info(f"deal_tracker(pipeline) → {len(pipeline)} open deals")
        return {"pipeline": pipeline, "count": len(pipeline)}

    elif action == "update":
        conn.execute("UPDATE leads SET stage=? WHERE id=?", (stage, lead_id))
        conn.commit()
        conn.close()
        log.info(f"deal_tracker(update) → {lead_id} moved to {stage}")
        return {"lead_id": lead_id, "new_stage": stage, "status": "updated"}

    elif action == "close":
        deal_id = deal_id or f"DEAL-{str(uuid.uuid4())[:4].upper()}"
        row = conn.execute("SELECT company FROM leads WHERE id=?", (lead_id,)).fetchone()
        company = row[0] if row else "Unknown"
        conn.execute("UPDATE leads SET stage='closed_won' WHERE id=?", (lead_id,))
        conn.execute(
            "INSERT OR IGNORE INTO deals (deal_id, lead_id, company, amount, stage, close_date) VALUES (?,?,?,?,?,?)",
            (deal_id, lead_id, company, amount, "closed_won", date.today().isoformat()),
        )
        conn.commit()
        conn.close()
        log.info(f"deal_tracker(close) → {deal_id} closed for ${amount}")
        return {"deal_id": deal_id, "lead_id": lead_id, "company": company,
                "amount": amount, "status": "closed_won"}

    conn.close()
    return {"error": f"Unknown action: {action}"}


# ══════════════════════════════════════════════════════════════════
#  TOOL 4: Notify Finance
# ══════════════════════════════════════════════════════════════════

def notify_finance(deal_id: str, company: str, amount: float) -> dict:
    """
    Send closed deal info to Finance agent via the standard message schema.
    Finance agent logs it to revenue table.
    """
    finance = FinanceAgent()
    msg = AgentMessage(
        sender="SALES",
        recipient="FINANCE",
        task_type="LOG_EXPENSE",   # Finance treats incoming revenue as a credit entry
        payload={
            "category": "revenue_credit",
            "amount": amount,
            "department": "sales",
            "month": date.today().strftime("%Y-%m"),
            "note": f"Closed deal {deal_id} with {company}",
        },
    )
    # Also write directly to revenue table for P&L accuracy
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO revenue (deal_id, company, amount, closed_date, source) VALUES (?,?,?,?,?)",
        (deal_id, company, amount, date.today().isoformat(), "SALES"),
    )
    conn.commit()
    conn.close()

    log.info(f"notify_finance({deal_id}, ${amount}) → sent to Finance + logged to revenue")
    return {
        "deal_id": deal_id,
        "company": company,
        "amount": amount,
        "status": "notified",
        "message": f"✅ Finance notified. Deal {deal_id} (${amount:,.0f}) logged to revenue.",
    }


# ══════════════════════════════════════════════════════════════════
#  TOOL 5: Objection Handler
# ══════════════════════════════════════════════════════════════════

OBJECTION_RESPONSES = {
    "too expensive": "We offer flexible pricing and a 30-day free trial — most teams see ROI within 60 days.",
    "already have a solution": "Happy to do a quick comparison. Many customers switched after seeing our integration options.",
    "not the right time": "No problem at all — can I check back in next quarter? I'll send some resources in the meantime.",
    "need to talk to my team": "Totally understand! I can prepare a one-pager for your team if that would help.",
    "not interested": "Appreciate the honesty! If anything changes, we're here. Mind if I follow up in 3 months?",
    "too complex": "We have a dedicated onboarding team and most customers are live in under a week.",
}

def objection_handler(objection: str) -> dict:
    """
    Match a sales objection to a prepared response.
    Escalates to CEO if the objection is unknown.
    """
    objection_lower = objection.lower().strip()

    # Try exact or partial match
    for key, response in OBJECTION_RESPONSES.items():
        if key in objection_lower:
            log.info(f"objection_handler matched: '{key}'")
            return {
                "objection": objection,
                "matched_key": key,
                "response": response,
                "escalate": False,
            }

    # No match — escalate
    log.warning(f"objection_handler: no match for '{objection}' — escalating")
    return {
        "objection": objection,
        "matched_key": None,
        "response": "Escalating to senior rep for a custom response.",
        "escalate": True,
        "escalation_reason": f"Unknown objection: '{objection}' — needs human or CEO review.",
    }


# ══════════════════════════════════════════════════════════════════
#  DECISION RULES
# ══════════════════════════════════════════════════════════════════

def rule_skip_low_score(lead: dict) -> dict | None:
    if lead.get("qualified") is False:
        return {
            "rule": "LOW_SCORE_SKIP",
            "lead_id": lead.get("id"),
            "company": lead.get("company"),
            "score": lead.get("score"),
            "action": "Moved to nurture sequence — no outreach sent.",
        }
    return None


def rule_escalate_objection(objection_result: dict) -> dict | None:
    if objection_result.get("escalate"):
        return {
            "rule": "UNKNOWN_OBJECTION_ESCALATE",
            "escalate_to": "CEO",
            "objection": objection_result.get("objection"),
            "message": objection_result.get("escalation_reason"),
        }
    return None


# ══════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ══════════════════════════════════════════════════════════════════

def route_tool(task_type: str, payload: dict) -> tuple[dict, dict | None]:
    task = task_type.upper()
    rule_notice = None

    if task == "SCORE_LEAD":
        result = lead_scorer(lead_id=payload.get("lead_id", ""), segment=payload.get("segment", ""))
        if "qualified" in result:
            rule_notice = rule_skip_low_score(result)

    elif task == "WRITE_MESSAGE":
        result = message_writer(
            company=payload.get("company", ""),
            contact=payload.get("contact", ""),
            pain_point=payload.get("pain_point", "efficiency"),
            fit=payload.get("fit", "medium"),
        )

    elif task == "PIPELINE":
        result = deal_tracker(action="pipeline")

    elif task == "UPDATE_DEAL":
        result = deal_tracker(action="update", lead_id=payload.get("lead_id", ""), stage=payload.get("stage", ""))

    elif task == "CLOSE_DEAL":
        result = deal_tracker(
            action="close",
            lead_id=payload.get("lead_id", ""),
            amount=float(payload.get("amount", 0)),
            deal_id=payload.get("deal_id", ""),
        )

    elif task == "NOTIFY_FINANCE":
        result = notify_finance(
            deal_id=payload.get("deal_id", ""),
            company=payload.get("company", ""),
            amount=float(payload.get("amount", 0)),
        )

    elif task == "HANDLE_OBJECTION":
        result = objection_handler(objection=payload.get("objection", ""))
        rule_notice = rule_escalate_objection(result)

    else:
        result = {"error": f"Unknown task_type: {task_type}"}

    return result, rule_notice


# ══════════════════════════════════════════════════════════════════
#  AGENT LOOP (CrewAI-compatible interface)
# ══════════════════════════════════════════════════════════════════

class SalesAgent:
    """
    Sales Agent — CrewAI-style role.
    Same 3-step interface as all other agents:
      1. receive()  — parse incoming message
      2. handle()   — run tools + apply decision rules
      3. respond()  — wrap result in standard message envelope
    """
    name = "SALES"

    def receive(self, message: AgentMessage) -> str:
        log.info(f"Received '{message.task_type}' from {message.sender}")
        return message.task_type

    def handle(self, message: AgentMessage) -> list[AgentMessage]:
        self.receive(message)
        responses = []

        try:
            result, rule_notice = route_tool(message.task_type, message.payload)
            status, error = "done", ""
        except Exception as e:
            result, rule_notice = {}, None
            status, error = "error", str(e)
            log.error(f"Tool error: {e}")

        responses.append(self.respond(
            recipient=message.sender,
            task_type=f"{message.task_type}_RESPONSE",
            payload=result,
            context=message.context,
            status=status,
            error=error,
        ))

        if rule_notice:
            recipient = rule_notice.get("escalate_to", message.sender)
            responses.append(self.respond(
                recipient=recipient,
                task_type="RULE_NOTICE" if recipient != "CEO" else "ESCALATION",
                payload=rule_notice,
                context={"triggered_by": message.task_type},
                status="pending" if recipient == "CEO" else "done",
            ))
            log.info(f"Rule notice → {recipient}: {rule_notice['rule']}")

        return responses

    def respond(self, recipient: str, task_type: str, payload: dict,
                context: dict = None, status: str = "done", error: str = "") -> AgentMessage:
        return AgentMessage(
            sender=self.name,
            recipient=recipient,
            task_type=task_type,
            payload=payload,
            context=context or {},
            status=status,
            error=error,
        )


# ─────────────────────── Quick self-test ───────────────────────────

if __name__ == "__main__":
    agent = SalesAgent()
    tests = [
        AgentMessage(sender="CEO", recipient="SALES", task_type="SCORE_LEAD",
                     payload={"lead_id": "L-002"}),   # score=42 → RULE 1
        AgentMessage(sender="CEO", recipient="SALES", task_type="SCORE_LEAD",
                     payload={"lead_id": "L-004"}),   # score=91 → passes
        AgentMessage(sender="CEO", recipient="SALES", task_type="WRITE_MESSAGE",
                     payload={"company": "Nexus Corp", "contact": "David Chen",
                              "pain_point": "growth", "fit": "high"}),
        AgentMessage(sender="CEO", recipient="SALES", task_type="WRITE_MESSAGE",
                     payload={"company": "Vertex Solutions", "contact": "Amy Wallace",
                              "pain_point": "efficiency", "fit": "medium"}),  # RULE 2
        AgentMessage(sender="CEO", recipient="SALES", task_type="PIPELINE", payload={}),
        AgentMessage(sender="CEO", recipient="SALES", task_type="HANDLE_OBJECTION",
                     payload={"objection": "too expensive"}),
        AgentMessage(sender="CEO", recipient="SALES", task_type="HANDLE_OBJECTION",
                     payload={"objection": "we use blockchain for everything"}),  # unknown → escalate
        AgentMessage(sender="CEO", recipient="SALES", task_type="CLOSE_DEAL",
                     payload={"lead_id": "L-003", "amount": 3600, "deal_id": "DEAL-105"}),
        AgentMessage(sender="CEO", recipient="SALES", task_type="NOTIFY_FINANCE",
                     payload={"deal_id": "DEAL-105", "company": "Corner Bookshop", "amount": 3600}),
    ]
    for msg in tests:
        print(f"\n{'='*55}\nTASK: {msg.task_type} | {msg.payload}")
        for r in agent.handle(msg):
            tag = "📨 ESCALATION" if r.task_type == "ESCALATION" else \
                  "🔔 RULE NOTICE" if r.task_type == "RULE_NOTICE" else "✅ RESPONSE"
            print(f"\n{tag} → {r.recipient}")
            print(json.dumps(r.to_dict(), indent=2))

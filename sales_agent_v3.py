"""
sales.py
─────────────────────────────────────────────────────────────────────────────
Sales Agent for the Kanosei virtual enterprise simulation.

Token tracking is handled by the shared TokenManager (token_manager.py).
GENERATE_PITCH and REVENUE_FORECAST are the two LLM-heavy tasks; they cost
more and are tracked accordingly.  All other tasks are lightweight logic/DB
operations with lower token costs.

Token cost summary for this agent
───────────────────────────────────────────────────────────────────────────
  Task                  Estimate   Notes
  ──────────────────    ────────   ──────────────────────────────────────
  QUALIFY_LEAD           700       Scoring algo + DB write
  GENERATE_PITCH        2500       Full LLM call (personalised outreach)
  LOG_REVENUE            400       DB write + two outbound messages
  PIPELINE_REPORT       1200       Aggregate query + structured summary
  REVENUE_FORECAST      1800       LLM probability estimates
  CUSTOMER_FEEDBACK      500       Forward to PM + ack
  ESCALATE_DISCOUNT      500       Threshold check + conditional route

Monthly budget: 200,000 tokens
Alerts fire at 80 % (WARNING) and 95 % (CRITICAL) consumed.
─────────────────────────────────────────────────────────────────────────────
"""

import uuid
import json
from datetime import datetime, timezone

# Import the shared token manager so Finance + Sales share the same state.
# In production you'd pass this in via dependency injection; here we import
# the singleton created in finance.py to keep both agents on the same object.
try:
    from finance import token_manager  # shared instance
except ImportError:
    from token_manager import TokenManager
    token_manager = TokenManager()

AGENT_NAME = "Sales"


# ── Stub Tools ────────────────────────────────────────────────────────────────
# Stand-ins for HubSpot CRM, LLM pitch writer, SQLite, email sender, etc.

def score_lead(name, deal_size, fit_score):
    """Simulate lead-scoring logic."""
    score = (deal_size / 1_000) * 0.6 + fit_score * 0.4
    return {
        "lead_name":  name,
        "score":      round(score, 2),
        "qualified":  score >= 50,
    }

def generate_pitch_copy(lead_name, product_info):
    """Simulate an LLM-generated personalised outreach email."""
    return (
        f"Hi {lead_name}, I wanted to reach out about {product_info}. "
        "Based on what you're building, I think we can cut your time-to-close "
        "by 30 %. Would a 20-minute call this week work for you?"
    )

def get_pipeline():
    """Stub pipeline data."""
    return {
        "leads":              12,
        "demos":               5,
        "closed":              3,
        "projected_revenue": 280_000,
        "deals": [
            {"name": "Acme Corp",    "stage": "demo",   "value": 48_000, "close_date": "2025-05-15"},
            {"name": "Globex Inc",   "stage": "closed", "value": 72_000, "close_date": "2025-04-01"},
            {"name": "Initech LLC",  "stage": "lead",   "value": 25_000, "close_date": "2025-06-01"},
        ],
    }

def estimate_forecast(pipeline):
    """Simulate revenue probability roll-up."""
    stage_weights = {"lead": 0.20, "demo": 0.55, "closed": 1.00}
    monthly  = sum(d["value"] * stage_weights.get(d["stage"], 0) for d in pipeline["deals"])
    quarterly = monthly * 3
    return {
        "projected_monthly":   round(monthly),
        "projected_quarterly": round(quarterly),
    }


# ── Message Helper ────────────────────────────────────────────────────────────

def send_message(recipient, task_type, payload, status="done", sender=AGENT_NAME):
    message = {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    sender,
        "recipient": recipient,
        "task_type": task_type,
        "context":   {},
        "payload":   payload,
        "status":    status,
        "error":     "",
    }
    print(f"\n[{sender} → {recipient}] {task_type}")
    print(json.dumps(message, indent=2))
    return message


# ── Token guard helpers ───────────────────────────────────────────────────────

def _reserve(task_type: str) -> tuple[bool, str]:
    ok, rid, err = token_manager.check_and_reserve(AGENT_NAME, task_type)
    if not ok:
        send_message("CEO", "BUDGET_EXCEEDED", err.get("payload", {}), status="error")
    return ok, rid


def _commit(rid: str, actual: int, sender: str):
    alerts = token_manager.commit(rid, actual)
    for alert in alerts:
        send_message("CEO", alert["task_type"], alert["payload"])


# ── Agent Loop ────────────────────────────────────────────────────────────────

def handle_message(message: dict):
    task_type = message["task_type"]
    payload   = message.get("payload", {})
    sender    = message.get("sender", "Unknown")

    print(f"\n[SalesAgent] Received: {task_type} from {sender}")

    # ── QUALIFY_LEAD ──────────────────────────────────────────────────────────
    if task_type == "QUALIFY_LEAD":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        result = score_lead(
            payload["name"],
            payload.get("deal_size", 0),
            payload.get("fit_score", 0),
        )
        send_message("CEO", "LEAD_QUALIFIED", result)

        _commit(rid, actual=650, sender=sender)

    # ── GENERATE_PITCH ────────────────────────────────────────────────────────
    elif task_type == "GENERATE_PITCH":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        pitch = generate_pitch_copy(
            payload.get("lead_name", ""),
            payload.get("product_info", "our product"),
        )
        send_message(sender, "PITCH_READY", {"pitch": pitch})

        # LLM calls have variable cost – report actual tokens from the API
        # response; here we use a realistic stub value.
        actual_tokens = payload.get("actual_tokens", 2_200)
        _commit(rid, actual=actual_tokens, sender=sender)

    # ── LOG_REVENUE ───────────────────────────────────────────────────────────
    elif task_type == "LOG_REVENUE":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        deal = {"deal_name": payload["deal_name"], "amount": payload["amount"],
                "closed_at": datetime.now(timezone.utc).isoformat()}

        send_message(sender, "REVENUE_LOGGED", deal)

        # Notify Finance so it can reconcile
        send_message("Finance", "VALIDATE_REVENUE", {
            "deal_name": payload["deal_name"],
            "amount":    payload["amount"],
        })

        _commit(rid, actual=380, sender=sender)

    # ── PIPELINE_REPORT ───────────────────────────────────────────────────────
    elif task_type == "PIPELINE_REPORT":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        pipeline = get_pipeline()
        send_message("CEO", "PIPELINE_SUMMARY", pipeline)

        _commit(rid, actual=1_100, sender=sender)

    # ── REVENUE_FORECAST ──────────────────────────────────────────────────────
    elif task_type == "REVENUE_FORECAST":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        pipeline = get_pipeline()
        forecast = estimate_forecast(pipeline)
        send_message("Finance", "FORECAST_REPORT", forecast)
        send_message("CEO",     "FORECAST_REPORT", forecast)

        _commit(rid, actual=1_650, sender=sender)

    # ── CUSTOMER_FEEDBACK ─────────────────────────────────────────────────────
    elif task_type == "CUSTOMER_FEEDBACK":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        feedback = {
            "feedback":         payload.get("feedback", ""),
            "objections":       payload.get("objections", []),
            "feature_requests": payload.get("feature_requests", []),
            "source":           payload.get("source", ""),
        }
        send_message("ProductManager", "FEEDBACK_FORWARDED", feedback)
        send_message(sender, "FEEDBACK_DELIVERED", {"forwarded": True})

        _commit(rid, actual=450, sender=sender)

    # ── ESCALATE_DISCOUNT ─────────────────────────────────────────────────────
    elif task_type == "ESCALATE_DISCOUNT":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        discount_pct = payload.get("discount_pct", 0)

        if discount_pct > 20:
            send_message("CEO", "DISCOUNT_APPROVAL_NEEDED", {
                "discount_pct": discount_pct,
                "deal_name":    payload.get("deal_name", ""),
                "reason":       payload.get("reason", ""),
                "message":      f"{discount_pct}% discount exceeds 20% auto-approval limit",
            }, status="pending")
        else:
            send_message(sender, "DISCOUNT_APPROVED", {
                "discount_pct":  discount_pct,
                "auto_approved": True,
            })

        _commit(rid, actual=430, sender=sender)

    # ── TOKEN_REPORT (CEO can request a usage snapshot at any time) ───────────
    elif task_type == "TOKEN_REPORT":
        report = token_manager.report(AGENT_NAME)
        send_message("CEO", "TOKEN_USAGE_REPORT", report)

    else:
        print(f"[SalesAgent] Unknown task_type: {task_type}")


# ── Test harness ──────────────────────────────────────────────────────────────

if _name_ == "_main_":
    test_messages = [
        # Qualify a lead
        {
            "sender": "CEO",
            "task_type": "QUALIFY_LEAD",
            "payload": {"name": "Jane Smith / Acme Corp", "deal_size": 50_000, "fit_score": 85},
        },
        # Generate a pitch
        {
            "sender": "CEO",
            "task_type": "GENERATE_PITCH",
            "payload": {"lead_name": "Jane Smith", "product_info": "Kanosei Enterprise Suite"},
        },
        # Log a closed deal
        {
            "sender": "CEO",
            "task_type": "LOG_REVENUE",
            "payload": {"deal_name": "Acme Corp", "amount": 48_000},
        },
        # Pipeline snapshot
        {
            "sender": "CEO",
            "task_type": "PIPELINE_REPORT",
            "payload": {},
        },
        # Revenue forecast
        {
            "sender": "CEO",
            "task_type": "REVENUE_FORECAST",
            "payload": {},
        },
        # Customer feedback forwarded to PM
        {
            "sender": "CEO",
            "task_type": "CUSTOMER_FEEDBACK",
            "payload": {
                "feedback":         "Onboarding is too slow",
                "objections":       ["price", "integration complexity"],
                "feature_requests": ["Slack integration", "CSV export"],
                "source":           "Acme Corp",
            },
        },
        # Discount below threshold → auto-approved
        {
            "sender": "AE",
            "task_type": "ESCALATE_DISCOUNT",
            "payload": {"discount_pct": 15, "deal_name": "Globex Inc", "reason": "Competitive pressure"},
        },
        # Discount above threshold → escalate to CEO
        {
            "sender": "AE",
            "task_type": "ESCALATE_DISCOUNT",
            "payload": {"discount_pct": 30, "deal_name": "Initech LLC", "reason": "Strategic account"},
        },
        # CEO requests a token usage report
        {
            "sender": "CEO",
            "task_type": "TOKEN_REPORT",
            "payload": {},
        },
    ]

    for msg in test_messages:
        handle_message(msg)
        print("\n" + "─" * 70)

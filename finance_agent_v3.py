"""
finance.py
─────────────────────────────────────────────────────────────────────────────
Finance Agent for the Kanosei virtual enterprise simulation.

Token tracking is handled by TokenManager (token_manager.py).
Every task handler:
  1. Calls tm.check_and_reserve() before doing any work.
  2. Does its work (stub tools below stand in for real APIs).
  3. Calls tm.commit() with the actual token cost when done.
  4. Forwards any budget alerts to the CEO automatically.
─────────────────────────────────────────────────────────────────────────────
"""

import uuid
import json
from datetime import datetime, timezone

from token_manager import TokenManager, TASK_COST_ESTIMATES


# ── Shared TokenManager instance ──────────────────────────────────────────────
# Import this same object in ceo.py, sales.py, etc. so all agents share state.
token_manager = TokenManager()

AGENT_NAME = "Finance"


# ── Stub Tools ────────────────────────────────────────────────────────────────
# Stand-ins for QuickBooks API, SQLite, Excel processor, etc.

def log_expense(amount, category, description):
    return {"saved": True, "amount": amount, "category": category, "description": description}

def generate_pl():
    return {"total_revenue": 100_000, "total_expenses": 60_000, "net_profit_loss": 40_000}

def get_burn_rate():
    return {"monthly_burn": 20_000, "cash_on_hand": 60_000, "runway_months": 3, "low_runway": True}

def allocate_budget(department, amount):
    return {"department": department, "allocated": amount, "saved": True}

def validate_revenue(deal_name, amount):
    return {"deal": deal_name, "amount": amount, "validated": True}


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


# ── Token guard helper ────────────────────────────────────────────────────────

def _reserve(task_type: str) -> tuple[bool, str]:
    """
    Attempt to reserve tokens for task_type.
    Returns (approved, reservation_id).
    If not approved, the BUDGET_EXCEEDED message is already printed/sent.
    """
    ok, rid, err = token_manager.check_and_reserve(AGENT_NAME, task_type)
    if not ok:
        send_message("CEO", "BUDGET_EXCEEDED", err.get("payload", {}), status="error")
    return ok, rid


def _commit(rid: str, actual: int, original_message_sender: str):
    """Commit actual token usage and forward any budget alerts to CEO."""
    alerts = token_manager.commit(rid, actual)
    for alert in alerts:
        send_message("CEO", alert["task_type"], alert["payload"])


# ── Agent Loop ────────────────────────────────────────────────────────────────

def handle_message(message: dict):
    task_type = message["task_type"]
    payload   = message.get("payload", {})
    sender    = message.get("sender", "Unknown")

    print(f"\n[FinanceAgent] Received: {task_type} from {sender}")

    # ── APPROVE_SPEND ─────────────────────────────────────────────────────────
    if task_type == "APPROVE_SPEND":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        amount = payload["amount"]

        if amount <= 10_000:
            send_message(sender, "SPEND_APPROVED", {
                "amount":       amount,
                "auto_approved": True,
            })
        else:
            send_message("CEO", "SPEND_ESCALATED", {
                "amount":        amount,
                "department":    payload.get("department", ""),
                "justification": payload.get("justification", ""),
                "reason":        f"${amount:,} exceeds $10,000 auto-approval threshold",
            }, status="pending")

        _commit(rid, actual=480, original_message_sender=sender)

    # ── LOG_EXPENSE ───────────────────────────────────────────────────────────
    elif task_type == "LOG_EXPENSE":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        result = log_expense(payload["amount"], payload["category"], payload.get("description", ""))
        send_message(sender, "EXPENSE_LOGGED", result)

        _commit(rid, actual=290, original_message_sender=sender)

    # ── GENERATE_PL ───────────────────────────────────────────────────────────
    elif task_type == "GENERATE_PL":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        send_message("CEO", "PL_REPORT", generate_pl())

        _commit(rid, actual=750, original_message_sender=sender)

    # ── BURN_RATE_ALERT ───────────────────────────────────────────────────────
    elif task_type == "BURN_RATE_ALERT":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        result = get_burn_rate()
        if result["low_runway"]:
            result["warning"] = f"Only {result['runway_months']} months of runway left!"
        send_message("CEO", "BURN_RATE_REPORT", result)

        _commit(rid, actual=580, original_message_sender=sender)

    # ── ALLOCATE_BUDGET ───────────────────────────────────────────────────────
    elif task_type == "ALLOCATE_BUDGET":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        result = allocate_budget(payload["department"], payload["amount"])
        send_message(sender, "BUDGET_ALLOCATED", result)

        _commit(rid, actual=270, original_message_sender=sender)

    # ── VALIDATE_REVENUE ──────────────────────────────────────────────────────
    elif task_type == "VALIDATE_REVENUE":
        ok, rid = _reserve(task_type)
        if not ok:
            return

        result = validate_revenue(payload["deal_name"], payload["amount"])
        if result["validated"]:
            send_message("Sales", "REVENUE_CONFIRMED", result)
        else:
            send_message("CEO", "REVENUE_DISCREPANCY", result)

        _commit(rid, actual=460, original_message_sender=sender)

    # ── TOKEN_REPORT (CEO can request a usage snapshot at any time) ───────────
    elif task_type == "TOKEN_REPORT":
        report = token_manager.report(AGENT_NAME)
        send_message("CEO", "TOKEN_USAGE_REPORT", report)

    else:
        print(f"[FinanceAgent] Unknown task_type: {task_type}")


# ── Test harness ──────────────────────────────────────────────────────────────

if _name_ == "_main_":
    test_messages = [
        # Auto-approve: under $10K
        {
            "sender": "HR",
            "task_type": "APPROVE_SPEND",
            "payload": {"amount": 5_000, "department": "HR", "justification": "Team offsite"},
        },
        # Escalate: over $10K
        {
            "sender": "Engineering",
            "task_type": "APPROVE_SPEND",
            "payload": {"amount": 15_000, "department": "Engineering", "justification": "New laptops"},
        },
        # Log an expense
        {
            "sender": "Operations",
            "task_type": "LOG_EXPENSE",
            "payload": {"amount": 1_200, "category": "SaaS", "description": "Figma seats"},
        },
        # Generate P&L
        {
            "sender": "CEO",
            "task_type": "GENERATE_PL",
            "payload": {},
        },
        # Burn rate check
        {
            "sender": "CEO",
            "task_type": "BURN_RATE_ALERT",
            "payload": {},
        },
        # Allocate budget
        {
            "sender": "CEO",
            "task_type": "ALLOCATE_BUDGET",
            "payload": {"department": "Marketing", "amount": 25_000},
        },
        # Validate revenue from Sales
        {
            "sender": "Sales",
            "task_type": "VALIDATE_REVENUE",
            "payload": {"deal_name": "Acme Corp", "amount": 48_000},
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

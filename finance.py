import uuid
import json
from datetime import datetime


# ── Stub Tools ────────────────────────────────────────────────────
# These return fake hardcoded data for now - tbd in real data

def log_expense(amount, category, description):
    return {"saved": True, "amount": amount, "category": category, "description": description}

def generate_pl():
    return {"total_revenue": 100000, "total_expenses": 60000, "net_profit_loss": 40000}

def get_burn_rate():
    return {"monthly_burn": 20000, "cash_on_hand": 60000, "runway_months": 3, "low_runway": True}

def allocate_budget(department, amount):
    return {"department": department, "allocated": amount, "saved": True}

def validate_revenue(deal_name, amount):
    return {"deal": deal_name, "amount": amount, "validated": True}


# ── Message Helper ────────────────────────────────────────────────
# Every message sent between agents uses this same format.
# "payload" is the only part that changes depending on the task.

def send_message(recipient, task_type, payload, status="done"):
    message = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "sender": "Finance",
        "recipient": recipient,
        "task_type": task_type,
        "context": {},
        "payload": payload,
        "status": status,
        "error": ""
    }
    print(f"\n[Finance → {recipient}] {task_type}")
    print(json.dumps(message, indent=2))


# ── Agent Loop ────────────────────────────────────────────────────
# This is the main function. It receives a message, reads the
# task_type, and routes it to the right logic.

def handle_message(message):
    task_type = message["task_type"]
    payload = message["payload"]

    print(f"\n[FinanceAgent] Received: {task_type}")

    if task_type == "APPROVE_SPEND":
        amount = payload["amount"]
        if amount <= 10000:
            send_message(message["sender"], "SPEND_APPROVED", {
                "amount": amount,
                "auto_approved": True
            })
        else:
            send_message("CEO", "SPEND_ESCALATED", {
                "amount": amount,
                "department": payload["department"],
                "justification": payload.get("justification", ""),
                "reason": f"${amount:,} exceeds $10,000 threshold"
            }, status="pending")

    elif task_type == "LOG_EXPENSE":
        result = log_expense(payload["amount"], payload["category"], payload.get("description", ""))
        send_message(message["sender"], "EXPENSE_LOGGED", result)

    elif task_type == "GENERATE_PL":
        send_message("CEO", "PL_REPORT", generate_pl())

    elif task_type == "BURN_RATE_ALERT":
        result = get_burn_rate()
        if result["low_runway"]:
            result["warning"] = f"Only {result['runway_months']} months of runway left!"
        send_message("CEO", "BURN_RATE_REPORT", result)

    elif task_type == "ALLOCATE_BUDGET":
        result = allocate_budget(payload["department"], payload["amount"])
        send_message(message["sender"], "BUDGET_ALLOCATED", result)

    elif task_type == "VALIDATE_REVENUE":
        result = validate_revenue(payload["deal_name"], payload["amount"])
        if result["validated"]:
            send_message("Sales", "REVENUE_CONFIRMED", result)
        else:
            send_message("CEO", "REVENUE_DISCREPANCY", result)

# ── Test it ───────────────────────────────────────────────────────
if __name__ == "__main__":
    handle_message({
        "sender": "CEO",
        "task_type": "APPROVE_SPEND",
        "payload": {"amount": 15000, "department": "Engineering", "justification": "New laptops"}
    })

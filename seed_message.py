"""
seed_message.py — Insert a test message into MongoDB for the Finance or Sales agent.
Run this once; the agent will pick it up on its next poll.

Usage:
  python seed_message.py finance    # seeds a GENERATE_PL_REPORT task
  python seed_message.py sales      # seeds a QUALIFY_LEAD task
  python seed_message.py pipeline   # seeds a PIPELINE_REPORT task
  python seed_message.py forecast   # seeds a CASH_FLOW_FORECAST task

Set MONGO_URI env var to point to your cluster (defaults to localhost).
"""

import sys
import uuid
from datetime import datetime, timezone
from pymongo import MongoClient

MONGO_URI = __import__("os").environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = __import__("os").environ.get("MONGO_DB", "kanosei")

MESSAGES = {
    "finance": {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    "CEO",
        "recipient": "FINANCE",
        "task_type": "GENERATE_PL_REPORT",
        "context":   {"priority": "high", "target_quarter": "Q2-2026"},
        "payload":   {"period": "Q2-2026", "include_forecast": True},
        "status":    "pending",
        "error":     "",
        "token_usage": {},
    },
    "sales": {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    "CEO",
        "recipient": "SALES",
        "task_type": "QUALIFY_LEAD",
        "context":   {"priority": "high"},
        "payload":   {"lead_id": "lead-001"},
        "status":    "pending",
        "error":     "",
        "token_usage": {},
    },
    "pipeline": {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    "CEO",
        "recipient": "SALES",
        "task_type": "PIPELINE_REPORT",
        "context":   {},
        "payload":   {},
        "status":    "pending",
        "error":     "",
        "token_usage": {},
    },
    "forecast": {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    "CEO",
        "recipient": "FINANCE",
        "task_type": "CASH_FLOW_FORECAST",
        "context":   {},
        "payload":   {
            "base_monthly_revenue_usd": 55000,
            "base_monthly_expense_usd": 42000,
            "months": 6,
        },
        "status":    "pending",
        "error":     "",
        "token_usage": {},
    },
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "finance"
    message = MESSAGES.get(mode)
    if not message:
        print(f"Unknown mode '{mode}'. Choose: {', '.join(MESSAGES.keys())}")
        sys.exit(1)

    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    result = db.messages.insert_one(message)
    print(f"Inserted {mode} message with _id: {result.inserted_id}")
    print(f"  id:        {message['id']}")
    print(f"  recipient: {message['recipient']}")
    print(f"  task_type: {message['task_type']}")
    print("Agent will pick this up on its next poll.")

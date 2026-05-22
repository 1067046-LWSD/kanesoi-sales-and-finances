"""
main.py — Finance + Sales Agent System entry point.

Modes:
  python main.py demo          Run the 'Launch SaaS Feature' scenario (no API key needed)
  python main.py run           Async in-memory bus mode (requires OLLAMA_READY)
  python main.py poll          MongoDB polling loop — mirrors Engineering agent exactly
  python main.py status        Print current tool-layer stats
  python main.py test          Run unit tests

MongoDB polling (python main.py poll):
  - Reads MONGO_URI / MONGO_DB env vars (defaults: localhost / kanosei)
  - Finance agent claims messages where recipient=FINANCE
  - Sales agent claims messages where recipient=SALES
  - Mirrors Engineering agent's claim_next_message / write_response / mark_source_done pattern
  - RecoverableError re-queues the message (status back to 'pending') and waits
"""

import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

MONGO_URI    = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB     = os.environ.get("MONGO_DB", "kanosei")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║   Finance + Sales Agent System — Group 5                ║
╚══════════════════════════════════════════════════════════╝
""")


# ---------------------------------------------------------------------------
# MongoDB helpers — identical pattern to Engineering agent
# ---------------------------------------------------------------------------

def get_db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URI)[MONGO_DB]


def claim_next_message(db, recipient: str):
    """Atomically claim a pending message for this agent."""
    return db.messages.find_one_and_update(
        {"recipient": recipient, "status": "pending"},
        {"$set": {"status": "in-progress"}},
        return_document=True,
    )


def write_response(db, response: dict):
    """Insert agent response as a new message document."""
    db.messages.insert_one(response)


def mark_source_done(db, message_id: str, status: str, error: str = ""):
    """Update the original message's status once finished."""
    db.messages.update_one(
        {"id": message_id},
        {"$set": {"status": status, "error": error}},
    )


# ---------------------------------------------------------------------------
# Polling loop — mirrors Engineering agent's __main__ block exactly
# ---------------------------------------------------------------------------

def run_poll():
    """
    Runs Finance and Sales agents in a shared MongoDB polling loop.
    Each agent claims messages addressed to it, processes them, writes the response.
    RecoverableError → message re-queued, agent sleeps and retries.
    """
    from agents.finance_agent import FinanceAgent
    from agents.sales_agent import SalesAgent

    print_banner()
    db = get_db()

    finance = FinanceAgent(bus=None, db=db)
    sales   = SalesAgent(bus=None, db=db)

    agents = {
        "FINANCE": finance,
        "SALES":   sales,
    }

    print(f"Finance + Sales agents started. Polling MongoDB every {POLL_INTERVAL}s...")

    while True:
        handled_any = False

        for recipient, agent in agents.items():
            message = claim_next_message(db, recipient)
            if message is None:
                continue

            message.pop("_id", None)
            handled_any = True
            print(f"\nPicked up message: {message['id']} ({message['task_type']}) → {recipient}")

            response = agent.handle_message(message)

            # None = RecoverableError — message was re-queued, skip writing response
            if response is None:
                print(f"Message {message['id']} re-queued pending token replenishment.")
                continue

            write_response(db, response)
            response.pop("_id", None)

            final_status = "done" if response.get("status") == "done" else "error"
            mark_source_done(db, message["id"], final_status, response.get("error", ""))

            print(f"Response written for {message['id']}. Status: {final_status}")
            print(json.dumps(response, indent=2, default=str))

        if not handled_any:
            time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Demo mode — no API key needed
# ---------------------------------------------------------------------------

async def run_demo():
    from bus.message_bus import bus
    from schema import AgentMessage
    from tools.finance_tools import (
        get_budget_status, generate_pl_report, monte_carlo_forecast, log_revenue
    )
    from tools.sales_tools import (
        qualify_lead, generate_pitch, close_deal, get_pipeline_report
    )

    print_banner()
    print("=== DEMO: Launch SaaS Feature Scenario ===\n")

    print("── Step 1: CEO checks Q2 budget ──")
    print(json.dumps(get_budget_status("Q2-2026"), indent=2))

    print("\n── Step 2: Finance runs Monte Carlo forecast ──")
    print(json.dumps(monte_carlo_forecast(55000, 42000, months=6, simulations=500), indent=2))

    print("\n── Step 3: Sales qualifies leads ──")
    for lead_id in ["lead-001", "lead-003", "lead-004"]:
        q = qualify_lead(lead_id)
        print(f"  {lead_id} ({q['company']}): score={q['score']} → {'QUALIFIED' if q['qualified'] else 'Not qualified'}")

    print("\n── Step 4: Sales generates pitch for AcmeCorp ──")
    print(generate_pitch("lead-001")["pitch"])

    print("\n── Step 5: Sales closes deal with MidCo Industries ──")
    deal = close_deal("lead-003", 18000.0, won=True)
    print(json.dumps(deal, indent=2))

    print("\n── Step 6: Finance logs revenue from closed deal ──")
    print(json.dumps(log_revenue(18000.0, deal.get("deal_id", "deal-demo"), "MidCo Industries"), indent=2))

    print("\n── Step 7: Finance generates P&L report ──")
    print(json.dumps(generate_pl_report("Q2-2026"), indent=2))

    print("\n── Step 8: Sales pipeline summary ──")
    print(json.dumps(get_pipeline_report(), indent=2))

    print("\n── Step 9: Session token stats ──")
    print(json.dumps(bus.get_session_stats(), indent=2))

    print("\nDemo complete.\n")


# ---------------------------------------------------------------------------
# Live async mode
# ---------------------------------------------------------------------------

async def run_live():
    api_key = True
    if False:
        print("ERROR: OLLAMA_READY not set. Use 'python main.py demo' for stub mode.")
        sys.exit(1)

    from bus.message_bus import bus
    from agents.finance_agent import FinanceAgent
    from agents.sales_agent import SalesAgent
    from schema import AgentMessage

    print_banner()
    print("Starting live agent loop...\n")

    finance = FinanceAgent(bus)
    sales   = SalesAgent(bus)

    async def seed_tasks():
        await asyncio.sleep(0.1)
        tasks = [
            AgentMessage(task_type="GENERATE_PL_REPORT", sender="CEO", recipient="FINANCE",
                         payload={"period": "Q2-2026"}),
            AgentMessage(task_type="QUALIFY_LEAD", sender="CEO", recipient="SALES",
                         payload={"lead_id": "lead-001"}),
            AgentMessage(task_type="PIPELINE_REPORT", sender="CEO", recipient="SALES", payload={}),
            AgentMessage(task_type="CASH_FLOW_FORECAST", sender="CEO", recipient="FINANCE",
                         payload={"base_monthly_revenue_usd": 55000, "base_monthly_expense_usd": 42000}),
        ]
        for t in tasks:
            await bus.send(t.to_dict())
        logger.info("Seeded 4 initial tasks")

    await asyncio.gather(seed_tasks(), finance.run(max_cycles=20), sales.run(max_cycles=20))

    bus.dump_log("logs/session_log.json")
    print("\nFinal Stats:")
    print(json.dumps(finance.get_status(), indent=2))
    print(json.dumps(sales.get_status(), indent=2))
    print(json.dumps(bus.get_session_stats(), indent=2))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if mode == "demo":
        asyncio.run(run_demo())
    elif mode == "run":
        asyncio.run(run_live())
    elif mode == "poll":
        run_poll()
    elif mode == "status":
        from tools.finance_tools import get_budget_status, generate_pl_report
        from tools.sales_tools import get_pipeline_report
        print_banner()
        print(json.dumps(get_budget_status("Q2-2026"), indent=2))
        print(json.dumps(generate_pl_report("Q2-2026"), indent=2))
        print(json.dumps(get_pipeline_report(), indent=2))
    elif mode == "test":
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
                           cwd=os.path.dirname(__file__))
        sys.exit(r.returncode)
    else:
        print(f"Unknown mode: {mode}. Use: demo | run | poll | status | test")

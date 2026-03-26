"""
Finance Agent — Week 6 (Final)
Framework: CrewAI-style role/task architecture (plain Python base, drop-in for CrewAI)

Tools (per design doc):
  1. budget_calculator     — check allocated vs spent from SQLite budgets table
  2. expense_tracker       — log and query expenses from SQLite expenses table
  3. pl_report_builder     — compute P&L from revenue + expenses tables
  4. cash_flow_simulator   — project runway for next 3-6 months
  5. burn_rate_alert       — flag if monthly burn exceeds threshold

Decision Rules:
  RULE 1: If dept spent >= allocated → block + escalate to CEO
  RULE 2: If any invoices are overdue → auto-escalate to CEO
"""

import json
import logging
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from db_setup import get_conn, init_db, DB_PATH
from message_schema import AgentMessage
import os

# Ensure DB exists
if not os.path.exists(DB_PATH):
    init_db()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [FINANCE] %(message)s")
log = logging.getLogger("finance")

BURN_RATE_THRESHOLD = 20000  # default monthly spend threshold for alerts


# ══════════════════════════════════════════════════════════════════
#  TOOL 1: Budget Calculator
# ══════════════════════════════════════════════════════════════════

def budget_calculator(department: str, quarter: str = "Q2") -> dict:
    """
    Look up budget for a department/quarter from SQLite.
    Returns allocated, spent, remaining, and utilization %.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT allocated, spent FROM budgets WHERE department=? AND quarter=?",
        (department.lower(), quarter),
    ).fetchone()
    conn.close()

    if not row:
        return {"error": f"No budget found for {department} in {quarter}"}

    allocated, spent = row
    remaining = allocated - spent
    result = {
        "department": department,
        "quarter": quarter,
        "allocated": allocated,
        "spent": spent,
        "remaining": remaining,
        "utilization_pct": round(spent / allocated * 100, 1) if allocated else 0,
    }
    log.info(f"budget_calculator({department}, {quarter}) → remaining={remaining}")
    return result


# ══════════════════════════════════════════════════════════════════
#  TOOL 2: Expense Tracker
# ══════════════════════════════════════════════════════════════════

def expense_tracker(action: str, department: str = "all",
                    category: str = "", amount: float = 0, month: str = "") -> dict:
    """
    action='log'   → add a new expense row
    action='query' → return expenses for a department/month
    action='total' → return total spend for a month
    """
    conn = get_conn()

    if action == "log":
        month = month or date.today().strftime("%Y-%m")
        conn.execute(
            "INSERT INTO expenses (category, amount, month, department) VALUES (?,?,?,?)",
            (category, amount, month, department),
        )
        conn.commit()
        conn.close()
        log.info(f"expense_tracker log: {category} ${amount} for {department} in {month}")
        return {"status": "logged", "category": category, "amount": amount, "month": month}

    elif action == "query":
        rows = conn.execute(
            "SELECT category, amount, month, department FROM expenses WHERE department=? OR ?='all'",
            (department, department),
        ).fetchall()
        conn.close()
        expenses = [{"category": r[0], "amount": r[1], "month": r[2], "department": r[3]} for r in rows]
        return {"department": department, "expenses": expenses, "count": len(expenses)}

    elif action == "total":
        month = month or date.today().strftime("%Y-%m")
        row = conn.execute(
            "SELECT SUM(amount) FROM expenses WHERE month=?", (month,)
        ).fetchone()
        conn.close()
        total = row[0] or 0
        return {"month": month, "total_spend": total}

    conn.close()
    return {"error": f"Unknown action: {action}"}


# ══════════════════════════════════════════════════════════════════
#  TOOL 3: P&L Report Builder
# ══════════════════════════════════════════════════════════════════

def pl_report_builder(quarter: str = "Q1", year: int = 2026) -> dict:
    """
    Build a P&L from SQLite revenue + expenses tables.
    Revenue = sum of deals closed in the quarter.
    COGS = 35% of revenue.
    OpEx = sum of all expenses in the quarter months.
    """
    quarter_months = {"Q1": ("01","02","03"), "Q2": ("04","05","06"),
                      "Q3": ("07","08","09"), "Q4": ("10","11","12")}
    months = quarter_months.get(quarter, ("01","02","03"))
    month_patterns = [f"{year}-{m}" for m in months]

    conn = get_conn()

    # Revenue: closed deals in this quarter
    placeholders = ",".join("?" * len(month_patterns))
    # Match closed_date year-month prefix
    like_clauses = " OR ".join(["closed_date LIKE ?"] * len(month_patterns))
    revenue_row = conn.execute(
        f"SELECT SUM(amount) FROM revenue WHERE {like_clauses}",
        [f"{p}%" for p in month_patterns],
    ).fetchone()
    revenue = revenue_row[0] or 0

    # OpEx: sum of expenses in those months
    opex_row = conn.execute(
        f"SELECT SUM(amount) FROM expenses WHERE month IN ({placeholders})",
        month_patterns,
    ).fetchone()
    opex = opex_row[0] or 0
    conn.close()

    cogs = round(revenue * 0.35, 2)
    gross_profit = revenue - cogs
    net_income = gross_profit - opex

    result = {
        "quarter": quarter,
        "year": year,
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "operating_expenses": opex,
        "net_income": net_income,
        "margin_pct": round(net_income / revenue * 100, 1) if revenue else 0,
    }
    log.info(f"pl_report_builder({quarter} {year}) → revenue={revenue}, net_income={net_income}")
    return result


# ══════════════════════════════════════════════════════════════════
#  TOOL 4: Cash Flow Simulator
# ══════════════════════════════════════════════════════════════════

def cash_flow_simulator(starting_cash: float = 200000, months: int = 6) -> dict:
    """
    Project cash flow and runway for the next N months.
    Uses avg monthly revenue from deals and avg monthly expenses from the DB.
    """
    conn = get_conn()

    # Avg monthly revenue (from all closed deals)
    rev_row = conn.execute("SELECT SUM(amount), COUNT(DISTINCT strftime('%Y-%m', closed_date)) FROM revenue").fetchone()
    total_rev, month_count = rev_row
    avg_monthly_revenue = (total_rev or 0) / max(month_count or 1, 1)

    # Avg monthly expenses
    exp_row = conn.execute("SELECT SUM(amount), COUNT(DISTINCT month) FROM expenses").fetchone()
    total_exp, exp_months = exp_row
    avg_monthly_expense = (total_exp or 0) / max(exp_months or 1, 1)
    conn.close()

    projections = []
    cash = starting_cash
    runway_months = None

    today = date.today()
    for i in range(1, months + 1):
        proj_date = (today + relativedelta(months=i)).strftime("%Y-%m")
        cash += avg_monthly_revenue - avg_monthly_expense
        net_flow = avg_monthly_revenue - avg_monthly_expense
        projections.append({
            "month": proj_date,
            "inflow": round(avg_monthly_revenue, 2),
            "outflow": round(avg_monthly_expense, 2),
            "net_flow": round(net_flow, 2),
            "ending_cash": round(cash, 2),
        })
        if cash <= 0 and runway_months is None:
            runway_months = i

    result = {
        "starting_cash": starting_cash,
        "avg_monthly_revenue": round(avg_monthly_revenue, 2),
        "avg_monthly_expense": round(avg_monthly_expense, 2),
        "runway_months": runway_months or f">{months} (cash positive)",
        "projections": projections,
    }
    log.info(f"cash_flow_simulator() → runway={result['runway_months']}, avg_burn={avg_monthly_expense:.0f}/mo")
    return result


# ══════════════════════════════════════════════════════════════════
#  TOOL 5: Burn Rate Alert
# ══════════════════════════════════════════════════════════════════

def burn_rate_alert(month: str = "", threshold: float = BURN_RATE_THRESHOLD) -> dict:
    """
    Sum all expenses for a given month and flag if over threshold.
    Defaults to current month.
    """
    month = month or date.today().strftime("%Y-%m")
    conn = get_conn()
    row = conn.execute("SELECT SUM(amount) FROM expenses WHERE month=?", (month,)).fetchone()
    # Also include pending invoices as upcoming burn
    inv_row = conn.execute("SELECT SUM(amount) FROM invoices WHERE status='pending'").fetchone()
    conn.close()

    monthly_spend = row[0] or 0
    pending_invoices = inv_row[0] or 0
    total_exposure = monthly_spend + pending_invoices
    is_high = total_exposure > threshold

    result = {
        "month": month,
        "logged_spend": monthly_spend,
        "pending_invoices": pending_invoices,
        "total_exposure": total_exposure,
        "threshold": threshold,
        "alert": is_high,
        "message": f"⚠️ Burn rate HIGH (${total_exposure:,.0f} vs ${threshold:,.0f} threshold)" if is_high
                   else f"✅ Burn rate OK (${total_exposure:,.0f} under ${threshold:,.0f} threshold)",
    }
    log.info(f"burn_rate_alert({month}) → total_exposure={total_exposure}, alert={is_high}")
    return result


# ══════════════════════════════════════════════════════════════════
#  TOOL 6: Invoice Tracker (bonus — supports RULE 2)
# ══════════════════════════════════════════════════════════════════

def get_overdue_invoices() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT invoice_id, vendor, amount, due_date, department FROM invoices WHERE status='overdue'"
    ).fetchall()
    conn.close()
    result = [{"invoice_id": r[0], "vendor": r[1], "amount": r[2], "due_date": r[3], "department": r[4]} for r in rows]
    log.info(f"get_overdue_invoices() → {len(result)} overdue")
    return result


# ══════════════════════════════════════════════════════════════════
#  DECISION RULES
# ══════════════════════════════════════════════════════════════════

def rule_block_overspent(budget: dict) -> dict | None:
    """RULE 1: Block + escalate if dept is at or over budget."""
    if "error" in budget:
        return None
    if budget["spent"] >= budget["allocated"]:
        dept = budget["department"]
        log.warning(f"RULE 1 fired: {dept} overspent — escalating to CEO")
        return {
            "rule": "OVERSPENT_BLOCK",
            "escalate_to": "CEO",
            "department": dept,
            "message": f"⛔ {dept} has exhausted its {budget['quarter']} budget. CEO approval required.",
            "budget": budget,
        }
    return None


def rule_escalate_overdue(invoices: list) -> dict | None:
    """RULE 2: Escalate overdue invoices to CEO."""
    if not invoices:
        return None
    total = sum(i["amount"] for i in invoices)
    log.warning(f"RULE 2 fired: {len(invoices)} overdue invoices totaling ${total}")
    return {
        "rule": "OVERDUE_INVOICES",
        "escalate_to": "CEO",
        "count": len(invoices),
        "total_amount": total,
        "invoices": invoices,
        "message": f"⚠️ {len(invoices)} overdue invoice(s) totaling ${total:,.0f} need attention.",
    }


# ══════════════════════════════════════════════════════════════════
#  TOOL ROUTER
# ══════════════════════════════════════════════════════════════════

def route_tool(task_type: str, payload: dict) -> tuple[dict, dict | None]:
    task = task_type.upper()
    escalation = None

    if task == "BUDGET_CHECK":
        result = budget_calculator(
            department=payload.get("department", "engineering"),
            quarter=payload.get("quarter", "Q2"),
        )
        escalation = rule_block_overspent(result)

    elif task == "LOG_EXPENSE":
        result = expense_tracker(
            action="log",
            department=payload.get("department", "all"),
            category=payload.get("category", "misc"),
            amount=float(payload.get("amount", 0)),
            month=payload.get("month", ""),
        )

    elif task == "QUERY_EXPENSES":
        result = expense_tracker(action="query", department=payload.get("department", "all"))

    elif task == "PL_REPORT":
        result = pl_report_builder(
            quarter=payload.get("quarter", "Q1"),
            year=int(payload.get("year", 2026)),
        )

    elif task == "CASH_FLOW":
        result = cash_flow_simulator(
            starting_cash=float(payload.get("starting_cash", 200000)),
            months=int(payload.get("months", 6)),
        )

    elif task == "BURN_RATE":
        result = burn_rate_alert(
            month=payload.get("month", ""),
            threshold=float(payload.get("threshold", BURN_RATE_THRESHOLD)),
        )

    elif task == "CHECK_OVERDUE":
        overdue = get_overdue_invoices()
        result = {"overdue_invoices": overdue, "count": len(overdue)}
        escalation = rule_escalate_overdue(overdue)

    else:
        result = {"error": f"Unknown task_type: {task_type}"}

    return result, escalation


# ══════════════════════════════════════════════════════════════════
#  AGENT LOOP (CrewAI-compatible interface)
# ══════════════════════════════════════════════════════════════════

class FinanceAgent:
    """
    Finance Agent — CrewAI-style role.
    Every agent exposes the same 3-step interface:
      1. receive()  — parse incoming message
      2. handle()   — run tools + apply decision rules
      3. respond()  — wrap result in standard message envelope
    """
    name = "FINANCE"

    def receive(self, message: AgentMessage) -> str:
        """Step 1: identify the task type."""
        log.info(f"Received '{message.task_type}' from {message.sender}")
        return message.task_type

    def handle(self, message: AgentMessage) -> list[AgentMessage]:
        """Steps 2 + 3: run tools, apply rules, return response(s)."""
        self.receive(message)
        responses = []

        try:
            result, escalation = route_tool(message.task_type, message.payload)
            status, error = "done", ""
        except Exception as e:
            result, escalation = {}, None
            status, error = "error", str(e)
            log.error(f"Tool error: {e}")

        # Primary response back to sender
        responses.append(self.respond(
            recipient=message.sender,
            task_type=f"{message.task_type}_RESPONSE",
            payload=result,
            context=message.context,
            status=status,
            error=error,
        ))

        # Escalation to CEO if rule fired (skip if sender is already CEO)
        if escalation and message.sender != "CEO":
            responses.append(self.respond(
                recipient="CEO",
                task_type="ESCALATION",
                payload=escalation,
                context={"triggered_by": message.task_type},
                status="pending",
            ))

        return responses

    def respond(self, recipient: str, task_type: str, payload: dict,
                context: dict = None, status: str = "done", error: str = "") -> AgentMessage:
        """Step 3: wrap result in the standard message envelope."""
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
    agent = FinanceAgent()
    tests = [
        AgentMessage(sender="CEO",  recipient="FINANCE", task_type="BUDGET_CHECK",
                     payload={"department": "marketing", "quarter": "Q1"}),  # overspent → RULE 1
        AgentMessage(sender="CEO",  recipient="FINANCE", task_type="CASH_FLOW",
                     payload={"starting_cash": 150000, "months": 6}),
        AgentMessage(sender="CEO",  recipient="FINANCE", task_type="BURN_RATE",
                     payload={"threshold": 40000}),
        AgentMessage(sender="PM",   recipient="FINANCE", task_type="CHECK_OVERDUE",
                     payload={}),                                            # overdue → RULE 2
        AgentMessage(sender="CEO",  recipient="FINANCE", task_type="PL_REPORT",
                     payload={"quarter": "Q1", "year": 2026}),
    ]
    for msg in tests:
        print(f"\n{'='*55}\nTASK: {msg.task_type}")
        for r in agent.handle(msg):
            tag = "📨 ESCALATION" if r.task_type == "ESCALATION" else "✅ RESPONSE"
            print(f"\n{tag} → {r.recipient}")
            print(json.dumps(r.to_dict(), indent=2))

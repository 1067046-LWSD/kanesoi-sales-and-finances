"""
tools/finance_tools.py — Real tools for the Finance Agent.
Week 6 deliverable: budget calculator, Monte Carlo sim, SQLite ledger, P&L generator.
"""

import sqlite3
import json
import random
import math
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "finance.db")


# ─── Database setup ────────────────────────────────────────────────────────────

def init_db():
    """Initialize SQLite ledger tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            type        TEXT NOT NULL,  -- 'revenue' | 'expense' | 'invoice'
            category    TEXT,
            amount_usd  REAL NOT NULL,
            description TEXT,
            agent       TEXT,
            deal_id     TEXT,
            quarter     TEXT
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            quarter     TEXT NOT NULL UNIQUE,
            total_usd   REAL NOT NULL,
            allocated   REAL DEFAULT 0,
            spent       REAL DEFAULT 0,
            created_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS token_costs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT,
            agent       TEXT,
            task_type   TEXT,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            total_tokens  INTEGER,
            cost_usd    REAL
        );
    """)
    conn.commit()

    # Seed Q2 2026 budget if not present
    c.execute("INSERT OR IGNORE INTO budgets (quarter, total_usd, allocated, spent, created_at) VALUES (?, ?, ?, ?, ?)",
              ("Q2-2026", 250000.0, 0.0, 0.0, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


# ─── Tool 1: Budget Calculator ─────────────────────────────────────────────────

def get_budget_status(quarter: str = "Q2-2026") -> dict:
    """Return current budget allocation and spend for a quarter."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT total_usd, allocated, spent FROM budgets WHERE quarter=?", (quarter,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"No budget found for {quarter}"}
    total, allocated, spent = row
    remaining = total - spent
    burn_pct = (spent / total * 100) if total > 0 else 0
    conn.close()
    return {
        "quarter": quarter,
        "total_budget_usd": total,
        "allocated_usd": allocated,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "burn_pct": round(burn_pct, 1),
        "alert": "HIGH_BURN" if burn_pct > 75 else ("MODERATE" if burn_pct > 50 else "OK"),
    }


def allocate_budget(quarter: str, amount_usd: float, category: str) -> dict:
    """Allocate budget to a category. CEO must approve if amount > $10,000."""
    if amount_usd > 10_000:
        return {
            "status": "REQUIRES_CEO_APPROVAL",
            "message": f"Allocation of ${amount_usd:,.2f} exceeds $10,000 threshold. Escalating to CEO.",
            "amount_usd": amount_usd,
            "category": category,
        }
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE budgets SET allocated = allocated + ? WHERE quarter=?", (amount_usd, quarter))
    conn.commit()
    conn.close()
    return {"status": "approved", "allocated_usd": amount_usd, "category": category, "quarter": quarter}


def log_expense(amount_usd: float, category: str, description: str, quarter: str = "Q2-2026", agent: str = "FINANCE") -> dict:
    """Record an expense in the ledger."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ts = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO transactions (ts, type, category, amount_usd, description, agent, quarter) VALUES (?,?,?,?,?,?,?)",
              (ts, "expense", category, amount_usd, description, agent, quarter))
    c.execute("UPDATE budgets SET spent = spent + ? WHERE quarter=?", (amount_usd, quarter))
    conn.commit()
    conn.close()
    return {"status": "logged", "amount_usd": amount_usd, "category": category, "ts": ts}


def log_revenue(amount_usd: float, deal_id: str, company: str, quarter: str = "Q2-2026") -> dict:
    """Record a closed deal's revenue from Sales Agent."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ts = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO transactions (ts, type, category, amount_usd, description, agent, deal_id, quarter) VALUES (?,?,?,?,?,?,?,?)",
              (ts, "revenue", "sales", amount_usd, f"Deal closed: {company}", "SALES", deal_id, quarter))
    conn.commit()
    conn.close()
    return {"status": "revenue_logged", "deal_id": deal_id, "amount_usd": amount_usd, "company": company, "ts": ts}


def log_token_cost(agent: str, task_type: str, input_tokens: int, output_tokens: int, total_tokens: int, cost_usd: float):
    """Persist per-call token usage to DB for cost reporting."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO token_costs (ts, agent, task_type, input_tokens, output_tokens, total_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(), agent, task_type, input_tokens, output_tokens, total_tokens, cost_usd))
    conn.commit()
    conn.close()


# ─── Tool 2: P&L Generator ─────────────────────────────────────────────────────

def generate_pl_report(quarter: str = "Q2-2026") -> dict:
    """Generate a Profit & Loss statement from the ledger."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    revenues = c.execute("SELECT SUM(amount_usd) FROM transactions WHERE type='revenue' AND quarter=?", (quarter,)).fetchone()[0] or 0
    expenses = c.execute("SELECT SUM(amount_usd) FROM transactions WHERE type='expense' AND quarter=?", (quarter,)).fetchone()[0] or 0

    # Breakdown by category
    revenue_rows = c.execute("SELECT category, SUM(amount_usd) FROM transactions WHERE type='revenue' AND quarter=? GROUP BY category", (quarter,)).fetchall()
    expense_rows = c.execute("SELECT category, SUM(amount_usd) FROM transactions WHERE type='expense' AND quarter=? GROUP BY category", (quarter,)).fetchall()

    # Token costs (token_costs table has no quarter column — aggregate all)
    token_cost = c.execute("SELECT SUM(cost_usd), SUM(total_tokens) FROM token_costs").fetchone()
    conn.close()

    gross_profit = revenues - expenses
    margin_pct = (gross_profit / revenues * 100) if revenues > 0 else 0

    return {
        "quarter": quarter,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "revenue_usd": round(revenues, 2),
        "expenses_usd": round(expenses, 2),
        "gross_profit_usd": round(gross_profit, 2),
        "gross_margin_pct": round(margin_pct, 1),
        "revenue_breakdown": {r[0]: round(r[1], 2) for r in revenue_rows},
        "expense_breakdown": {e[0]: round(e[1], 2) for e in expense_rows},
        "total_ai_cost_usd": round(token_cost[0] or 0, 4),
        "total_tokens_used": int(token_cost[1] or 0),
        "status": "profit" if gross_profit > 0 else "loss",
    }


# ─── Tool 3: Monte Carlo Cash Flow Simulation ─────────────────────────────────

def monte_carlo_forecast(
    base_revenue_usd: float,
    base_expense_usd: float,
    months: int = 6,
    simulations: int = 1000,
    revenue_volatility: float = 0.15,
    expense_volatility: float = 0.08,
) -> dict:
    """
    Run Monte Carlo simulation to forecast cash flow distribution.
    Returns P10/P50/P90 outcomes, runway estimate, and burn-rate risk.
    """
    random.seed(42)
    final_balances = []

    for _ in range(simulations):
        balance = 0
        for month in range(months):
            rev = base_revenue_usd * (1 + random.gauss(0.02, revenue_volatility))
            exp = base_expense_usd * (1 + random.gauss(0.01, expense_volatility))
            balance += max(rev, 0) - max(exp, 0)
        final_balances.append(balance)

    final_balances.sort()
    n = len(final_balances)
    p10 = final_balances[int(n * 0.10)]
    p50 = final_balances[int(n * 0.50)]
    p90 = final_balances[int(n * 0.90)]

    avg_monthly_burn = base_expense_usd - base_revenue_usd
    runway_months = None
    if avg_monthly_burn > 0:
        current_cash = base_revenue_usd * 3  # assume 3-month cash reserve
        runway_months = round(current_cash / avg_monthly_burn, 1)

    risk_level = "LOW"
    if p10 < 0:
        risk_level = "HIGH" if p50 < 0 else "MODERATE"

    return {
        "months_simulated": months,
        "simulations": simulations,
        "base_monthly_revenue_usd": base_revenue_usd,
        "base_monthly_expense_usd": base_expense_usd,
        "p10_outcome_usd": round(p10, 2),
        "p50_outcome_usd": round(p50, 2),
        "p90_outcome_usd": round(p90, 2),
        "avg_monthly_burn_usd": round(avg_monthly_burn, 2),
        "runway_months": runway_months,
        "risk_level": risk_level,
        "recommendation": (
            "Reduce OPEX immediately" if risk_level == "HIGH"
            else "Monitor closely" if risk_level == "MODERATE"
            else "Cash flow healthy"
        ),
    }


# ─── Tool 4: Audit Report ──────────────────────────────────────────────────────

def generate_audit_report(quarter: str = "Q2-2026") -> dict:
    """Full audit: all transactions, token costs, anomalies."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    txns = c.execute("SELECT ts, type, category, amount_usd, description, agent, deal_id FROM transactions WHERE quarter=? ORDER BY ts", (quarter,)).fetchall()
    token_rows = c.execute("SELECT agent, task_type, SUM(input_tokens), SUM(output_tokens), SUM(cost_usd) FROM token_costs GROUP BY agent, task_type").fetchall()
    conn.close()

    transactions = [
        {"ts": r[0], "type": r[1], "category": r[2], "amount_usd": r[3],
         "description": r[4], "agent": r[5], "deal_id": r[6]}
        for r in txns
    ]

    # Flag anomalies: single expense > $5000
    anomalies = [t for t in transactions if t["type"] == "expense" and t["amount_usd"] > 5000]

    token_summary = [
        {"agent": r[0], "task_type": r[1], "total_input": r[2],
         "total_output": r[3], "total_cost_usd": round(r[4], 4)}
        for r in token_rows
    ]

    return {
        "quarter": quarter,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_count": len(transactions),
        "transactions": transactions,
        "anomalies_detected": len(anomalies),
        "anomalies": anomalies,
        "token_cost_by_agent": token_summary,
    }


# Initialize DB on import
init_db()

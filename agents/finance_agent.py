"""
agents/finance_agent.py — Finance Agent (Group 5)

Responsibilities:
  - Budget allocation and tracking
  - Invoice tracking / P&L generation
  - Monte Carlo cash flow forecasting
  - Burn-rate alerts (escalates >$10K to CEO)
  - Revenue logging from Sales Agent
  - Token cost logging per call
"""

import json
import logging
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.base_agent import BaseAgent, CEO_APPROVAL_THRESHOLD_USD
from schema import AgentMessage, TokenUsage
from tools.finance_tools import (
    get_budget_status, allocate_budget, log_expense, log_revenue,
    log_token_cost, generate_pl_report, monte_carlo_forecast, generate_audit_report,
)

logger = logging.getLogger("finance_agent")

FINANCE_SYSTEM_PROMPT = """You are the Finance Agent for an AI-simulated enterprise.

Your role:
- Oversee budgets, P&L, cash flow, and financial forecasting
- Flag burn-rate risks and escalate spend > $10,000 to the CEO
- Log all revenue received from the Sales Agent
- Run Monte Carlo simulations for probabilistic forecasting
- Produce clear, concise financial reports

Decision rules:
1. Any single expense or allocation > $10,000 REQUIRES CEO approval before proceeding.
2. If projected monthly burn exceeds 80% of budget, send a BUDGET_ALERT immediately.
3. Revenue from Sales is logged immediately and reflected in P&L.
4. All outputs must include token usage and cost for auditability.

Always respond in JSON format matching the requested task output.
Keep responses under 900 tokens — you have a max_tokens budget of 1000.
"""


class FinanceAgent(BaseAgent):

    def __init__(self, bus, db=None):
        super().__init__(name="FINANCE", bus=bus, system_prompt=FINANCE_SYSTEM_PROMPT, db=db)

    async def handle(self, message: AgentMessage):
        task = message.task_type
        logger.info(f"[FINANCE] Handling: {task} from {message.sender}")

        handlers = {
            "GENERATE_PL_REPORT": self._handle_pl_report,
            "BUDGET_APPROVAL":    self._handle_budget_approval,
            "CASH_FLOW_FORECAST": self._handle_forecast,
            "REVENUE_LOG":        self._handle_revenue_log,
            "AUDIT_REPORT":       self._handle_audit,
            "MONTE_CARLO_SIM":    self._handle_monte_carlo,
        }
        handler = handlers.get(task)
        if handler:
            return await handler(message)

        logger.warning(f"[FINANCE] Unknown task: {task}")
        return message.reply({"error": f"Unknown task: {task}"}, status="error")

    # ─── Handlers ────────────────────────────────────────────────────────────

    async def _handle_pl_report(self, msg: AgentMessage):
        quarter = msg.payload.get("period", "Q2-2026")
        raw_pl = generate_pl_report(quarter)
        budget = get_budget_status(quarter)

        prompt = f"""Generate a concise CFO-style P&L summary for {quarter}.

Raw data:
{json.dumps(raw_pl, indent=2)}

Budget status:
{json.dumps(budget, indent=2)}

Return JSON with keys: summary (string), health (good/warning/critical),
key_risks (list of strings), recommendations (list of strings), pl_data (the raw data).
"""
        # analyst role handles planning/reporting; pl_report op
        result, usage = self.call_llm_structured(
            prompt, task_type="GENERATE_PL_REPORT", op="pl_report", preferred_role="analyst"
        )
        log_token_cost("FINANCE", "GENERATE_PL_REPORT",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        payload = {**result, "pl_data": raw_pl, "budget": budget}
        reply = msg.reply(payload, status="done")
        reply.token_usage = usage.to_dict()

        if budget.get("burn_pct", 0) > 75:
            alert = AgentMessage(
                task_type="BUDGET_ALERT", sender="FINANCE", recipient="CEO",
                payload={
                    "quarter": quarter, "burn_pct": budget["burn_pct"],
                    "spent_usd": budget["spent_usd"],
                    "total_budget_usd": budget["total_budget_usd"],
                    "alert": "HIGH_BURN — immediate action required",
                },
                status="pending",
            )
            await self.send(alert)
            logger.warning("[FINANCE] HIGH BURN ALERT sent to CEO")

        return reply

    async def _handle_budget_approval(self, msg: AgentMessage):
        amount   = msg.payload.get("amount_usd", 0)
        category = msg.payload.get("category", "general")
        quarter  = msg.payload.get("quarter", "Q2-2026")

        if amount > CEO_APPROVAL_THRESHOLD_USD:
            ceo = await self.escalate_to_ceo(
                "BUDGET_APPROVAL",
                f"Requested allocation of ${amount:,.2f} exceeds $10,000 threshold",
                {"amount_usd": amount, "category": category},
            )
            if not ceo.get("approved"):
                return msg.reply({"approved": False, "reason": "CEO rejected"}, status="done")

        # executor role handles actual allocations
        result = allocate_budget(quarter, amount, category)
        return msg.reply(result, status="done")

    async def _handle_forecast(self, msg: AgentMessage):
        base_rev = msg.payload.get("base_monthly_revenue_usd", 50000)
        base_exp = msg.payload.get("base_monthly_expense_usd", 45000)
        months   = msg.payload.get("months", 6)

        mc = monte_carlo_forecast(base_rev, base_exp, months=months)

        prompt = f"""Interpret this Monte Carlo cash flow simulation result as a CFO would.

{json.dumps(mc, indent=2)}

Return JSON with: narrative (2-3 sentences), risk_level, top_actions (list of 3 short strings).
"""
        # analyst does the forecasting interpretation
        result, usage = self.call_llm_structured(
            prompt, task_type="CASH_FLOW_FORECAST", op="forecast", preferred_role="analyst"
        )
        log_token_cost("FINANCE", "CASH_FLOW_FORECAST",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({**mc, "llm_interpretation": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_revenue_log(self, msg: AgentMessage):
        """Sales Agent sends this directly after every closed_won deal."""
        deal_id = msg.payload.get("deal_id", "")
        company = msg.payload.get("company", "Unknown")
        amount  = msg.payload.get("deal_value_usd", 0)
        quarter = msg.payload.get("quarter", "Q2-2026")

        result = log_revenue(amount, deal_id, company, quarter)
        logger.info(f"[FINANCE] Revenue logged: ${amount:,.2f} from {company} (deal {deal_id})")
        return msg.reply(result, status="done")

    async def _handle_audit(self, msg: AgentMessage):
        quarter = msg.payload.get("quarter", "Q2-2026")
        audit = generate_audit_report(quarter)

        prompt = f"""Review this audit report and flag any concerns.

{json.dumps(audit, indent=2)}

Return JSON with: findings (list), severity (low/medium/high), token_cost_summary (string).
"""
        # reporter role handles audit summaries (shorter output)
        result, usage = self.call_llm_structured(
            prompt, task_type="AUDIT_REPORT", op="audit", preferred_role="reporter"
        )
        log_token_cost("FINANCE", "AUDIT_REPORT",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({**audit, "llm_analysis": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_monte_carlo(self, msg: AgentMessage):
        params = msg.payload
        mc = monte_carlo_forecast(
            base_revenue_usd=params.get("base_revenue_usd", 50000),
            base_expense_usd=params.get("base_expense_usd", 45000),
            months=params.get("months", 6),
            simulations=params.get("simulations", 1000),
        )
        return msg.reply(mc, status="done")

"""
tests/test_agents.py — Unit + integration tests for Finance & Sales agents.
Week 9 deliverable. Run with: python -m pytest tests/ -v

Tests:
  - Finance tools: budget, P&L, Monte Carlo
  - Sales tools: lead qualification, pitch generation, deal close
  - Schema: message serialization
  - Integration: CEO → Finance → Sales → Finance revenue loop
"""

import sys
import os
import json
import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema import AgentMessage, TokenUsage
from tools.finance_tools import (
    get_budget_status, allocate_budget, log_expense,
    log_revenue, generate_pl_report, monte_carlo_forecast,
)
from tools.sales_tools import (
    qualify_lead, generate_pitch, close_deal,
    get_pipeline_report, identify_upsell_opportunities,
)


# ─── Schema Tests ──────────────────────────────────────────────────────────────

class TestAgentMessage(unittest.TestCase):

    def test_message_serialization(self):
        msg = AgentMessage(
            task_type="GENERATE_PL_REPORT",
            sender="CEO",
            recipient="FINANCE",
            payload={"period": "Q2-2026"},
            context={"quarter": "Q2"},
        )
        d = msg.to_dict()
        self.assertEqual(d["sender"], "CEO")
        self.assertEqual(d["recipient"], "FINANCE")
        self.assertEqual(d["status"], "pending")
        self.assertIn("id", d)
        self.assertIn("timestamp", d)

    def test_message_reply(self):
        msg = AgentMessage(
            task_type="QUALIFY_LEAD",
            sender="CEO",
            recipient="SALES",
            payload={"lead_id": "lead-001"},
        )
        reply = msg.reply({"score": 85, "qualified": True})
        self.assertEqual(reply.sender, "SALES")
        self.assertEqual(reply.recipient, "CEO")
        self.assertEqual(reply.status, "done")

    def test_message_roundtrip(self):
        original = AgentMessage(
            task_type="REVENUE_LOG",
            sender="SALES",
            recipient="FINANCE",
            payload={"deal_id": "deal-001", "amount_usd": 24000},
        )
        restored = AgentMessage.from_dict(original.to_dict())
        self.assertEqual(original.id, restored.id)
        self.assertEqual(original.task_type, restored.task_type)
        self.assertEqual(original.payload["amount_usd"], restored.payload["amount_usd"])

    def test_token_usage_calculation(self):
        usage = TokenUsage.from_response({"input_tokens": 600, "output_tokens": 400})
        self.assertEqual(usage.total_tokens, 1000)
        self.assertLessEqual(usage.total_tokens, 1000)  # within budget
        self.assertGreater(usage.cost_usd, 0)
        expected_cost = round((600 * 3.0 / 1_000_000) + (400 * 15.0 / 1_000_000), 6)
        self.assertAlmostEqual(usage.cost_usd, expected_cost, places=5)

    def test_token_usage_within_budget(self):
        """Token budget must not exceed 1000."""
        usage = TokenUsage.from_response({"input_tokens": 500, "output_tokens": 500})
        self.assertLessEqual(usage.total_tokens, 1000)

    def test_json_serialization(self):
        msg = AgentMessage(
            task_type="BUDGET_ALERT",
            sender="FINANCE",
            recipient="CEO",
            payload={"burn_pct": 82.5},
        )
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["payload"]["burn_pct"], 82.5)


# ─── Finance Tool Tests ────────────────────────────────────────────────────────

class TestFinanceTools(unittest.TestCase):

    def test_budget_status_returns_dict(self):
        result = get_budget_status("Q2-2026")
        self.assertIn("total_budget_usd", result)
        self.assertIn("remaining_usd", result)
        self.assertIn("burn_pct", result)
        self.assertIn("alert", result)

    def test_budget_status_valid_quarter(self):
        result = get_budget_status("Q2-2026")
        self.assertGreater(result["total_budget_usd"], 0)
        self.assertGreaterEqual(result["remaining_usd"], 0)

    def test_allocate_budget_under_threshold(self):
        result = allocate_budget("Q2-2026", 5000.0, "engineering")
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["allocated_usd"], 5000.0)

    def test_allocate_budget_requires_ceo_over_10k(self):
        result = allocate_budget("Q2-2026", 15000.0, "marketing")
        self.assertEqual(result["status"], "REQUIRES_CEO_APPROVAL")
        self.assertIn("10,000", result["message"])

    def test_log_expense(self):
        result = log_expense(500.0, "software", "Slack subscription", "Q2-2026")
        self.assertEqual(result["status"], "logged")
        self.assertEqual(result["amount_usd"], 500.0)

    def test_log_revenue(self):
        result = log_revenue(24000.0, "deal-test-001", "TestCorp", "Q2-2026")
        self.assertEqual(result["status"], "revenue_logged")
        self.assertEqual(result["amount_usd"], 24000.0)
        self.assertEqual(result["company"], "TestCorp")

    def test_pl_report_structure(self):
        report = generate_pl_report("Q2-2026")
        required_keys = ["quarter", "revenue_usd", "expenses_usd", "gross_profit_usd",
                         "gross_margin_pct", "status", "total_ai_cost_usd"]
        for key in required_keys:
            self.assertIn(key, report)

    def test_monte_carlo_basic(self):
        result = monte_carlo_forecast(50000, 45000, months=3, simulations=100)
        self.assertIn("p10_outcome_usd", result)
        self.assertIn("p50_outcome_usd", result)
        self.assertIn("p90_outcome_usd", result)
        # P10 should be <= P50 <= P90
        self.assertLessEqual(result["p10_outcome_usd"], result["p50_outcome_usd"])
        self.assertLessEqual(result["p50_outcome_usd"], result["p90_outcome_usd"])

    def test_monte_carlo_high_burn_alert(self):
        # Expenses > Revenue → high burn
        result = monte_carlo_forecast(10000, 50000, months=6, simulations=200)
        self.assertIn(result["risk_level"], ["MODERATE", "HIGH"])

    def test_monte_carlo_healthy(self):
        # Revenue >> Expenses → low risk
        result = monte_carlo_forecast(100000, 20000, months=6, simulations=200)
        self.assertEqual(result["risk_level"], "LOW")


# ─── Sales Tool Tests ──────────────────────────────────────────────────────────

class TestSalesTools(unittest.TestCase):

    def test_qualify_lead_enterprise_high_score(self):
        result = qualify_lead("lead-001")  # AcmeCorp, enterprise, confirmed budget
        self.assertIn("score", result)
        self.assertIn("qualified", result)
        self.assertGreaterEqual(result["score"], 50)
        self.assertTrue(result["qualified"])

    def test_qualify_lead_smb_low_score(self):
        result = qualify_lead("lead-005")  # LocalBiz, smb, no budget, unknown timeline
        self.assertIn("score", result)
        self.assertLess(result["score"], 50)
        self.assertFalse(result["qualified"])

    def test_qualify_lead_returns_recommendation(self):
        result = qualify_lead("lead-003")
        self.assertIn("recommendation", result)
        self.assertIn(result["recommendation"], ["Proceed to demo", "Nurture further", "Deprioritize"])

    def test_generate_pitch_enterprise(self):
        result = generate_pitch("lead-001")
        self.assertIn("pitch", result)
        self.assertIn("company", result)
        self.assertGreater(len(result["pitch"]), 50)
        self.assertIn("AcmeCorp", result["pitch"])

    def test_generate_pitch_smb(self):
        result = generate_pitch("lead-002")
        self.assertIn("pitch", result)
        self.assertIn("StartupXYZ", result["pitch"])

    def test_generate_pitch_invalid_lead(self):
        result = generate_pitch("lead-9999")
        self.assertIn("error", result)

    def test_pipeline_report_structure(self):
        report = get_pipeline_report()
        self.assertIn("pipeline_by_stage", result := report)
        self.assertIn("total_pipeline_value_usd", result)
        self.assertIn("generated_at", result)

    def test_pipeline_has_stages(self):
        report = get_pipeline_report()
        # Must have at least prospect stage with our seeded data
        stages = list(report["pipeline_by_stage"].keys())
        self.assertGreater(len(stages), 0)

    def test_close_deal_won(self):
        result = close_deal("lead-002", 6000.0, won=True)
        self.assertEqual(result["stage"], "closed_won")
        self.assertIn("deal_id", result)
        self.assertEqual(result["deal_value_usd"], 6000.0)

    def test_close_deal_lost(self):
        result = close_deal("lead-005", 0.0, won=False)
        self.assertEqual(result["stage"], "closed_lost")

    def test_upsell_opportunities_list(self):
        # Close a deal first so upsell has data
        close_deal("lead-003", 18000.0, won=True)
        opps = identify_upsell_opportunities()
        self.assertIsInstance(opps, list)
        for opp in opps:
            self.assertIn("company", opp)
            self.assertIn("upsell_to", opp)
            self.assertGreater(opp["upsell_value_usd"], opp["current_deal_usd"])


# ─── Integration Test: Full Business Scenario ─────────────────────────────────

class TestIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Integration test: simulates the "Launch SaaS Feature" scenario
    without actual LLM calls (all patched via MagicMock).
    CEO → Finance (budget) → Sales (qualify + pitch + close) → Finance (revenue)
    """

    def setUp(self):
        # Patch anthropic at the module level so agents can import cleanly
        self._anthropic_patcher = patch("agents.base_agent._CREWAI_AVAILABLE", True)
        self._client_patcher = patch("agents.base_agent._requests")
        self._anthropic_patcher.start()
        self._client_patcher.start()

        from bus.message_bus import MessageBus
        self.bus = MessageBus()

    def tearDown(self):
        self._anthropic_patcher.stop()
        self._client_patcher.stop()

    async def test_full_scenario(self):
        from agents.finance_agent import FinanceAgent
        from agents.sales_agent import SalesAgent

        finance = FinanceAgent(self.bus)
        sales = SalesAgent(self.bus)

        stub_finance = (
            {"summary": "Healthy Q2", "health": "good", "key_risks": [], "recommendations": []},
            TokenUsage(input_tokens=400, output_tokens=300, total_tokens=700, cost_usd=0.0057),
        )
        stub_sales = (
            {"strategy": "Fast close", "next_step": "Send demo", "priority": "high"},
            TokenUsage(input_tokens=350, output_tokens=250, total_tokens=600, cost_usd=0.0043),
        )

        finance.call_llm_structured = MagicMock(return_value=stub_finance)
        sales.call_llm_structured = MagicMock(return_value=stub_sales)

        # Step 1: CEO requests P&L from Finance
        pl_req = AgentMessage(
            task_type="GENERATE_PL_REPORT",
            sender="CEO", recipient="FINANCE",
            payload={"period": "Q2-2026"},
        )
        result = await finance.handle(pl_req)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "done")
        self.assertIn("pl_data", result.payload)

        # Step 2: CEO asks Sales to qualify a lead
        qual_req = AgentMessage(
            task_type="QUALIFY_LEAD",
            sender="CEO", recipient="SALES",
            payload={"lead_id": "lead-001"},
        )
        result = await sales.handle(qual_req)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "done")
        self.assertIn("score", result.payload)

        # Step 3: Sales closes a deal (auto-sends REVENUE_LOG to Finance via bus)
        close_req = AgentMessage(
            task_type="CLOSE_DEAL",
            sender="CEO", recipient="SALES",
            payload={"lead_id": "lead-004", "final_value_usd": 35000.0, "won": True},
            context={"quarter": "Q2-2026"},
        )
        result = await sales.handle(close_req)
        self.assertEqual(result.status, "done")
        self.assertIn("deal_id", result.payload)

        # Step 4: Finance handles the REVENUE_LOG that Sales sent
        revenue_msg = AgentMessage(
            task_type="REVENUE_LOG",
            sender="SALES", recipient="FINANCE",
            payload={"deal_id": "deal-test", "company": "GlobalTech",
                     "deal_value_usd": 35000.0, "quarter": "Q2-2026"},
        )
        result = await finance.handle(revenue_msg)
        self.assertEqual(result.status, "done")
        self.assertEqual(result.payload["status"], "revenue_logged")

    async def test_budget_escalation_flow(self):
        from agents.finance_agent import FinanceAgent
        finance = FinanceAgent(self.bus)

        # Over $10K → CEO escalation (simulated approval for < $50K)
        approval_req = AgentMessage(
            task_type="BUDGET_APPROVAL",
            sender="CEO", recipient="FINANCE",
            payload={"amount_usd": 25000.0, "category": "cloud_infra", "quarter": "Q2-2026"},
        )
        result = await finance.handle(approval_req)
        self.assertEqual(result.status, "done")

    async def test_pipeline_report_flow(self):
        from agents.sales_agent import SalesAgent

        sales = SalesAgent(self.bus)
        sales.call_llm_structured = MagicMock(return_value=(
            {"executive_summary": "Strong pipeline.", "pipeline_health": "healthy",
             "top_priority": "Close enterprise deals"},
            TokenUsage(input_tokens=300, output_tokens=200, total_tokens=500, cost_usd=0.004),
        ))

        req = AgentMessage(
            task_type="PIPELINE_REPORT",
            sender="CEO", recipient="SALES",
            payload={},
        )
        result = await sales.handle(req)
        self.assertEqual(result.status, "done")
        self.assertIn("pipeline_by_stage", result.payload)

    async def test_monte_carlo_request_flow(self):
        """Finance agent handles MONTE_CARLO_SIM request end-to-end."""
        from agents.finance_agent import FinanceAgent
        finance = FinanceAgent(self.bus)

        req = AgentMessage(
            task_type="MONTE_CARLO_SIM",
            sender="CEO", recipient="FINANCE",
            payload={"base_revenue_usd": 55000, "base_expense_usd": 42000,
                     "months": 6, "simulations": 200},
        )
        result = await finance.handle(req)
        self.assertEqual(result.status, "done")
        self.assertIn("p50_outcome_usd", result.payload)
        self.assertIn("risk_level", result.payload)

    async def test_sales_upsell_flow(self):
        """Sales agent identifies upsell targets from closed deals."""
        from agents.sales_agent import SalesAgent

        sales = SalesAgent(self.bus)
        sales.call_llm_structured = MagicMock(return_value=(
            {"top_3": ["lead-003"], "total_upsell_value_usd": 32400,
             "outreach_strategy": "Reach out with ROI data."},
            TokenUsage(input_tokens=300, output_tokens=200, total_tokens=500, cost_usd=0.0038),
        ))

        req = AgentMessage(
            task_type="UPSELL",
            sender="CEO", recipient="SALES",
            payload={},
        )
        result = await sales.handle(req)
        self.assertEqual(result.status, "done")
        self.assertIn("opportunities", result.payload)


# ─── Chaos Tests ───────────────────────────────────────────────────────────────

class TestChaos(unittest.IsolatedAsyncioTestCase):
    """Week 10: test agent resilience to bad inputs and edge cases."""

    def setUp(self):
        self._anthropic_patcher = patch("agents.base_agent._CREWAI_AVAILABLE", True)
        self._client_patcher = patch("agents.base_agent._requests")
        self._anthropic_patcher.start()
        self._client_patcher.start()

        from bus.message_bus import MessageBus
        self.bus = MessageBus()

    def tearDown(self):
        self._anthropic_patcher.stop()
        self._client_patcher.stop()

    async def test_missing_lead_id(self):
        from agents.sales_agent import SalesAgent
        sales = SalesAgent(self.bus)
        msg = AgentMessage(
            task_type="QUALIFY_LEAD",
            sender="CEO",
            recipient="SALES",
            payload={},  # missing lead_id
        )
        result = await sales.handle(msg)
        self.assertEqual(result.status, "error")
        self.assertIn("error", result.payload)

    async def test_invalid_lead_id(self):
        from agents.sales_agent import SalesAgent
        sales = SalesAgent(self.bus)
        msg = AgentMessage(
            task_type="GENERATE_PITCH",
            sender="CEO",
            recipient="SALES",
            payload={"lead_id": "lead-DOESNOTEXIST"},
        )
        result = await sales.handle(msg)
        self.assertEqual(result.status, "error")

    async def test_unknown_task_type(self):
        from agents.finance_agent import FinanceAgent
        finance = FinanceAgent(self.bus)
        msg = AgentMessage(
            task_type="DO_SOMETHING_WEIRD",
            sender="CEO",
            recipient="FINANCE",
            payload={},
        )
        result = await finance.handle(msg)
        self.assertIn("error", result.payload)

    async def test_zero_revenue_pl(self):
        """P&L should not crash with no transactions."""
        report = generate_pl_report("Q9-9999")
        self.assertEqual(report["revenue_usd"], 0)
        self.assertEqual(report["expenses_usd"], 0)

    def test_monte_carlo_zero_revenue(self):
        """Monte Carlo shouldn't crash with zero revenue."""
        result = monte_carlo_forecast(0, 10000, months=3, simulations=50)
        self.assertIn("risk_level", result)
        self.assertEqual(result["risk_level"], "HIGH")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# TokenBudget tests — matches Engineering agent's budget system
# ---------------------------------------------------------------------------

class TestTokenBudget(unittest.TestCase):
    """Tests for the per-role token budget system (mirrors Engineering agent)."""

    def setUp(self):
        from agents.base_agent import TokenBudget, DEFAULT_BUDGETS, OP_COSTS, RecoverableError
        self.TokenBudget = TokenBudget
        self.DEFAULT_BUDGETS = DEFAULT_BUDGETS
        self.OP_COSTS = OP_COSTS
        self.RecoverableError = RecoverableError

    def test_initial_budgets(self):
        tb = self.TokenBudget()
        self.assertEqual(tb.remaining("analyst"), self.DEFAULT_BUDGETS["analyst"])
        self.assertEqual(tb.remaining("executor"), self.DEFAULT_BUDGETS["executor"])
        self.assertEqual(tb.remaining("reporter"), self.DEFAULT_BUDGETS["reporter"])

    def test_deduct_reduces_balance(self):
        tb = self.TokenBudget()
        before = tb.remaining("analyst")
        tb.deduct("analyst", "pl_report")
        self.assertEqual(tb.remaining("analyst"), before - self.OP_COSTS["pl_report"])

    def test_can_afford_true(self):
        tb = self.TokenBudget()
        self.assertTrue(tb.can_afford("analyst", "qualify"))

    def test_can_afford_false_when_empty(self):
        tb = self.TokenBudget()
        tb.budgets["analyst"] = 0
        self.assertFalse(tb.can_afford("analyst", "qualify"))

    def test_role_for_returns_preferred_when_affordable(self):
        tb = self.TokenBudget()
        role = tb.role_for("analyst", "qualify")
        self.assertEqual(role, "analyst")

    def test_role_for_falls_back_when_preferred_empty(self):
        tb = self.TokenBudget()
        tb.budgets["analyst"] = 0
        role = tb.role_for("analyst", "qualify")
        self.assertIn(role, ["executor", "reporter"])

    def test_recoverable_error_when_all_empty(self):
        tb = self.TokenBudget()
        tb.budgets = {"analyst": 0, "executor": 0, "reporter": 0}
        with self.assertRaises(self.RecoverableError):
            tb.role_for("analyst", "qualify", db=None)

    def test_reset_restores_full_budgets(self):
        tb = self.TokenBudget()
        tb.budgets = {"analyst": 0, "executor": 0, "reporter": 0}
        tb.reset()
        self.assertEqual(tb.budgets, self.DEFAULT_BUDGETS)

    def test_deduct_does_not_go_negative(self):
        tb = self.TokenBudget()
        tb.budgets["reporter"] = 10
        tb.deduct("reporter", "pl_report")  # cost > remaining
        self.assertEqual(tb.remaining("reporter"), 0)

    def test_disable_token_budget_env_skips_check(self):
        import os
        os.environ["DISABLE_TOKEN_BUDGET"] = "1"
        tb = self.TokenBudget()
        tb.budgets = {"analyst": 0, "executor": 0, "reporter": 0}
        # Should NOT raise — budget check is disabled
        role = tb.role_for("analyst", "qualify")
        self.assertEqual(role, "analyst")
        del os.environ["DISABLE_TOKEN_BUDGET"]

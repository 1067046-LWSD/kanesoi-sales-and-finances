"""
agents/sales_agent.py — Sales Agent (Group 5: Aarav, Saketh, Nimeshikaa)

Responsibilities:
  - Prospect and qualify leads (BANT scoring)
  - Generate personalized pitches per buyer segment
  - Handle objections and negotiate/close deals
  - Upsell existing accounts
  - Log revenue to Finance Agent after close
  - Report pipeline to CEO
"""

import json
import logging
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.base_agent import BaseAgent
from schema import AgentMessage, TokenUsage
from tools.sales_tools import (
    qualify_lead, generate_pitch, close_deal,
    get_pipeline_report, identify_upsell_opportunities,
)
from tools.finance_tools import log_token_cost

logger = logging.getLogger("sales_agent")

SALES_SYSTEM_PROMPT = """You are the Sales Agent for an AI-simulated enterprise.

Your role:
- Qualify leads using BANT criteria (Budget, Authority, Need, Timeline)
- Generate compelling, personalized pitches tailored to each buyer's segment
- Handle objections diplomatically and professionally
- Negotiate pricing and close deals
- Identify and pursue upsell opportunities with existing customers
- Report pipeline metrics and revenue to CEO and Finance Agent

Decision rules:
1. Only pursue leads with qualification score >= 50.
2. Escalate deals > $50,000 to CEO for final approval.
3. After every closed_won deal, immediately send a REVENUE_LOG message to FINANCE.
4. Prioritize enterprise leads (higher deal value) when pipeline is full.
5. Generate personalized pitches — never use generic templates verbatim.

Always respond in JSON. Keep responses under 900 tokens.
"""


class SalesAgent(BaseAgent):

    def __init__(self, bus, db=None):
        super().__init__(name="SALES", bus=bus, system_prompt=SALES_SYSTEM_PROMPT, db=db)

    async def handle(self, message: AgentMessage):
        task = message.task_type
        logger.info(f"[SALES] Handling: {task} from {message.sender}")

        handlers = {
            "QUALIFY_LEAD":    self._handle_qualify,
            "GENERATE_PITCH":  self._handle_pitch,
            "CLOSE_DEAL":      self._handle_close,
            "UPSELL":          self._handle_upsell,
            "PIPELINE_REPORT": self._handle_pipeline,
            "DEMO_REQUEST":    self._handle_demo,
        }
        handler = handlers.get(task)
        if handler:
            return await handler(message)

        logger.warning(f"[SALES] Unknown task: {task}")
        return message.reply({"error": f"Unknown task: {task}"}, status="error")

    # ─── Handlers ────────────────────────────────────────────────────────────

    async def _handle_qualify(self, msg: AgentMessage):
        lead_id = msg.payload.get("lead_id", "")
        if not lead_id:
            return msg.reply({"error": "lead_id required"}, status="error")

        qual = qualify_lead(lead_id)

        prompt = f"""You are a senior sales rep reviewing a lead qualification result.

Lead data:
{json.dumps(qual, indent=2)}

Provide a brief strategic note. Return JSON with:
  strategy (1-2 sentences on how to approach this lead),
  next_step (string: one concrete next action),
  priority (high/medium/low).
"""
        # analyst role does the strategic thinking
        result, usage = self.call_llm_structured(
            prompt, task_type="QUALIFY_LEAD", op="qualify", preferred_role="analyst"
        )
        log_token_cost("SALES", "QUALIFY_LEAD",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({**qual, "llm_strategy": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_pitch(self, msg: AgentMessage):
        lead_id   = msg.payload.get("lead_id", "")
        objection = msg.payload.get("objection", None)

        base_pitch = generate_pitch(lead_id)
        if "error" in base_pitch:
            return msg.reply(base_pitch, status="error")

        objection_context = (
            f"\n\nThe prospect raised this objection: '{objection}'. Address it directly."
            if objection else ""
        )
        prompt = f"""You are a skilled sales rep. Enhance this pitch to be more compelling and natural.{objection_context}

Base pitch:
{base_pitch['pitch']}

Segment: {base_pitch['segment']}

Return JSON with:
  enhanced_pitch (improved version),
  subject_line (for email),
  objection_response (if objection was provided, else null),
  confidence_score (0-100).
"""
        # executor does the pitch writing (heavier creative output)
        result, usage = self.call_llm_structured(
            prompt, task_type="GENERATE_PITCH", op="pitch", preferred_role="executor"
        )
        log_token_cost("SALES", "GENERATE_PITCH",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({**base_pitch, "llm_enhanced": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_close(self, msg: AgentMessage):
        lead_id     = msg.payload.get("lead_id", "")
        final_value = msg.payload.get("final_value_usd", 0)
        won         = msg.payload.get("won", True)

        if final_value > 50_000:
            ceo = await self.escalate_to_ceo(
                "CLOSE_DEAL",
                f"Deal of ${final_value:,.2f} requires CEO sign-off",
                {"lead_id": lead_id, "amount_usd": final_value},
            )
            if not ceo.get("approved"):
                return msg.reply({"status": "blocked", "reason": "CEO did not approve"}, status="done")

        result = close_deal(lead_id, final_value, won)

        # After every closed_won → immediately send REVENUE_LOG to FINANCE (no CEO relay)
        if won and "deal_id" in result:
            revenue_msg = AgentMessage(
                task_type="REVENUE_LOG",
                sender="SALES",
                recipient="FINANCE",
                payload={
                    "deal_id":        result["deal_id"],
                    "company":        result["company"],
                    "deal_value_usd": final_value,
                    "quarter":        msg.context.get("quarter", "Q2-2026"),
                },
                context={"auto_logged": True},
                status="pending",
            )
            await self.send(revenue_msg)
            logger.info(f"[SALES] REVENUE_LOG → FINANCE: ${final_value:,.2f} — {result['company']}")

        return msg.reply(result, status="done")

    async def _handle_upsell(self, msg: AgentMessage):
        opportunities = identify_upsell_opportunities()

        prompt = f"""You are reviewing upsell opportunities for an enterprise SaaS company.

Opportunities:
{json.dumps(opportunities, indent=2)}

Return JSON with:
  top_3 (list of lead_ids to prioritize),
  total_upsell_value_usd (sum of those 3),
  outreach_strategy (1-2 sentences).
"""
        # analyst identifies the best opportunities
        result, usage = self.call_llm_structured(
            prompt, task_type="UPSELL", op="upsell", preferred_role="analyst"
        )
        log_token_cost("SALES", "UPSELL",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({"opportunities": opportunities, "llm_analysis": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_pipeline(self, msg: AgentMessage):
        report = get_pipeline_report()

        prompt = f"""Summarize this sales pipeline report for the CEO.

{json.dumps(report, indent=2)}

Return JSON with:
  executive_summary (2 sentences),
  pipeline_health (healthy/at_risk/critical),
  top_priority (one action to take now).
"""
        # reporter generates the summary (short output)
        result, usage = self.call_llm_structured(
            prompt, task_type="PIPELINE_REPORT", op="pipeline", preferred_role="reporter"
        )
        log_token_cost("SALES", "PIPELINE_REPORT",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({**report, "llm_summary": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

    async def _handle_demo(self, msg: AgentMessage):
        lead_id = msg.payload.get("lead_id", "")
        qual    = qualify_lead(lead_id)

        prompt = f"""Prepare a 30-minute demo agenda for this prospect.

Lead info:
{json.dumps(qual, indent=2)}

Return JSON with:
  agenda (list of 5 items with time_minutes and topic),
  key_value_props (list of 3),
  success_criteria (what does a successful demo look like).
"""
        # executor builds the demo plan
        result, usage = self.call_llm_structured(
            prompt, task_type="DEMO_REQUEST", op="demo", preferred_role="executor"
        )
        log_token_cost("SALES", "DEMO_REQUEST",
                       usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.cost_usd)

        reply = msg.reply({"lead": qual, "demo_plan": result}, status="done")
        reply.token_usage = usage.to_dict()
        return reply

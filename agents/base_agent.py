"""
agents/base_agent.py — Base class for all Finance + Sales agents.

Mirrors the Engineering agent's mold:
  - TokenBudget with per-role budgets, fallback, and HR escalation
  - RecoverableError for when all budgets are exhausted
  - MongoDB-compatible message polling (same claim/write/mark pattern)
  - Anthropic API calls with full token tracking
  - CEO escalation for spend > $10K
"""

import json
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

try:
    import requests as _requests
    _CREWAI_AVAILABLE = True
except ImportError:
    _requests = None  # type: ignore
    _CREWAI_AVAILABLE = False

from schema import AgentMessage, TokenUsage

logger = logging.getLogger(__name__)

MAX_TOKENS = 1000
MODEL = os.environ.get("OLLAMA_MODEL", "ollama/llama3.1:latest")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
CEO_APPROVAL_THRESHOLD_USD = 10_000

# ---------------------------------------------------------------------------
# Token budget system — mirrors Engineering agent exactly
# ---------------------------------------------------------------------------
# Each agent role gets a virtual token budget per task. When exhausted:
#   1. A fallback role absorbs the work.
#   2. If ALL roles exhausted → TOKEN_REQUEST sent to HR → RecoverableError.

DEFAULT_BUDGETS = {
    "analyst":  5000,   # finance analyst / sales strategist (planner, high budget)
    "executor": 4000,   # deal closer / budget allocator (does the heavy work)
    "reporter": 2000,   # report generation (shorter outputs)
}

# Rough token cost per operation type (same pattern as Engineering)
OP_COSTS = {
    "qualify":   600,
    "pitch":     700,
    "close":     500,
    "upsell":    600,
    "pipeline":  400,
    "demo":      500,
    "pl_report": 700,
    "forecast":  800,
    "budget":    500,
    "audit":     600,
    "monte_carlo": 800,
}


class RecoverableError(Exception):
    """
    Raised when all agent roles are out of token budget.
    The polling loop re-queues the message (sets status back to 'pending')
    and waits for HR to replenish — identical to Engineering agent behavior.
    """
    pass


class TokenBudget:
    """
    Per-task token budget tracker.
    Call reset() at the start of each new top-level message (same as Eng agent).
    """

    def __init__(self):
        self.budgets = dict(DEFAULT_BUDGETS)

    def reset(self):
        self.budgets = dict(DEFAULT_BUDGETS)

    def remaining(self, role: str) -> int:
        return self.budgets.get(role, 0)

    def deduct(self, role: str, op: str):
        cost = OP_COSTS.get(op, 400)
        self.budgets[role] = max(0, self.budgets.get(role, 0) - cost)

    def can_afford(self, role: str, op: str) -> bool:
        return self.budgets.get(role, 0) >= OP_COSTS.get(op, 400)

    def role_for(self, preferred_role: str, op: str, db=None) -> str:
        """
        Return the role that should handle this op — preferred if it can afford
        it, otherwise the first fallback that can. If nobody can, send a
        TOKEN_REQUEST to HR and raise RecoverableError (mirrors Eng agent).
        """
        if os.environ.get("DISABLE_TOKEN_BUDGET"):
            return preferred_role

        if self.can_afford(preferred_role, op):
            self.deduct(preferred_role, op)
            return preferred_role

        # Try fallbacks in priority order
        priority = ["analyst", "executor", "reporter"]
        for role in priority:
            if role != preferred_role and self.can_afford(role, op):
                logger.warning(
                    f"[TokenBudget] '{preferred_role}' out of budget for '{op}' "
                    f"— falling back to '{role}'"
                )
                self.deduct(role, op)
                return role

        # All roles exhausted — ask HR for more tokens
        self._request_tokens_from_hr(db)
        raise RecoverableError(
            f"All roles out of token budget for op '{op}'. TOKEN_REQUEST sent to HR."
        )

    def _request_tokens_from_hr(self, db):
        """Insert a TOKEN_REQUEST message into MongoDB for the HR agent."""
        request = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": "FINANCE_SALES",
            "recipient": "HR",
            "task_type": "TOKEN_REQUEST",
            "context": {},
            "payload": {
                "reason": "All Finance/Sales agent roles have exhausted their token budgets mid-task.",
                "requested_budgets": DEFAULT_BUDGETS,
            },
            "status": "pending",
            "error": "",
            "token_usage": {},
        }
        if db is not None:
            try:
                db.messages.insert_one(request)
                request.pop("_id", None)
                logger.warning(f"[TokenBudget] TOKEN_REQUEST sent to HR: {request['id']}")
            except Exception as e:
                logger.error(f"[TokenBudget] Failed to send TOKEN_REQUEST to HR: {e}")
        else:
            logger.warning("[TokenBudget] TOKEN_REQUEST would be sent to HR (no DB connected)")


# ---------------------------------------------------------------------------
# Base Agent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """
    All Finance/Sales agents inherit from this.
    Provides:
      - LLM calls with automatic token tracking + budget enforcement
      - CEO escalation for high-spend decisions
      - In-memory async bus send/receive (for local runs)
      - MongoDB-compatible handle_message() (for production polling loop)
    """

    def __init__(self, name: str, bus, system_prompt: str, db=None):
        self.name = name
        self.bus = bus
        self.system_prompt = system_prompt
        self.db = db  # MongoDB handle — set when running in production polling mode
        self.tokens = TokenBudget()

        self.client = _requests  # requests module used for Ollama HTTP calls

        self.conversation_history: list[dict] = []
        self._running = False
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._total_tokens = 0
        self._total_cost_usd = 0.0

        if bus is not None:
            bus.register(name)
        logger.info(f"[{name}] Initialized")

    # ─── LLM Interface ──────────────────────────────────────────────────────

    def call_llm(
        self,
        user_message: str,
        task_type: str = "UNKNOWN",
        op: str = "pl_report",
        preferred_role: str = "analyst",
        retries: int = 2,
    ) -> tuple[str, TokenUsage]:
        """
        Call Anthropic API with budget enforcement.
        Deducts from the appropriate role budget; raises RecoverableError
        if all budgets are exhausted (same as Engineering agent).
        """
        # Enforce token budget before making the call
        _ = self.tokens.role_for(preferred_role, op, db=self.db)

        self.conversation_history.append({"role": "user", "content": user_message})

        # Build message list with system prompt prepended as first user turn
        # (Ollama /api/chat supports a system role directly)
        ollama_messages = [{"role": "system", "content": self.system_prompt}]
        ollama_messages += self.conversation_history

        model_name = MODEL.replace("ollama/", "")  # strip prefix if present

        for attempt in range(retries + 1):
            try:
                start = time.time()
                resp = _requests.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={
                        "model": model_name,
                        "messages": ollama_messages,
                        "stream": False,
                        "options": {"num_predict": MAX_TOKENS},
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                elapsed = time.time() - start

                data = resp.json()
                text = data.get("message", {}).get("content", "")

                # Ollama returns prompt_eval_count / eval_count for token counts
                in_tok  = data.get("prompt_eval_count", 0)
                out_tok = data.get("eval_count", 0)
                usage = TokenUsage.from_response(
                    {"input_tokens": in_tok, "output_tokens": out_tok},
                    model=model_name,
                )

                self._total_tokens += usage.total_tokens
                self._total_cost_usd += usage.cost_usd
                self.conversation_history.append({"role": "assistant", "content": text})

                logger.info(
                    f"[{self.name}] LLM OK | task={task_type} op={op} role={preferred_role} | "
                    f"in={in_tok} out={out_tok} total={usage.total_tokens} | {elapsed:.2f}s"
                )
                return text, usage

            except Exception as e:
                logger.warning(f"[{self.name}] Ollama error attempt {attempt+1}: {e}")
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt)

        return "", TokenUsage()

    def call_llm_structured(
        self,
        user_message: str,
        task_type: str = "UNKNOWN",
        op: str = "pl_report",
        preferred_role: str = "analyst",
    ) -> tuple[dict, TokenUsage]:
        """Call LLM and parse JSON response. Falls back to raw text on parse failure."""
        text, usage = self.call_llm(user_message, task_type, op=op, preferred_role=preferred_role)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        try:
            return json.loads(cleaned), usage
        except json.JSONDecodeError:
            logger.warning(f"[{self.name}] JSON parse failed, returning raw")
            return {"raw": text}, usage

    # ─── CEO Escalation ─────────────────────────────────────────────────────

    async def escalate_to_ceo(self, task_type: str, reason: str, payload: dict) -> dict:
        """Send escalation to CEO and wait for approval (simulated)."""
        msg = AgentMessage(
            task_type=f"ESCALATION_{task_type}",
            sender=self.name,
            recipient="CEO",
            payload={"reason": reason, **payload},
            context={"escalated": True, "escalated_at": datetime.now(timezone.utc).isoformat()},
            status="pending",
        )
        await self.bus.send(msg.to_dict())
        logger.info(f"[{self.name}] Escalated to CEO: {reason}")
        # Simulate: approve if under $50K
        approved = payload.get("amount_usd", 0) < 50_000
        return {
            "approved": approved,
            "ceo_note": "Approved via simulated CEO" if approved else "Rejected: over budget cap",
        }

    # ─── Bus Helpers (async in-memory mode) ─────────────────────────────────

    async def send(self, message: AgentMessage):
        await self.bus.send(message.to_dict())

    async def receive(self, timeout: float = 30.0) -> Optional[AgentMessage]:
        data = await self.bus.receive(self.name, timeout=timeout)
        if data:
            return AgentMessage.from_dict(data)
        return None

    # ─── MongoDB Polling Mode (mirrors Engineering agent) ───────────────────

    def handle_message(self, message: dict) -> dict:
        """
        Synchronous handler for MongoDB polling loop — same interface as
        EngineeringAgent.handle_message(). Called by the main polling loop.
        Resets token budget for each new top-level message.
        """
        import asyncio
        self.tokens.reset()  # fresh budget per task, same as Eng agent

        try:
            msg = AgentMessage.from_dict(message)
            # Run async handler in sync context
            loop = asyncio.new_event_loop()
            response_msg = loop.run_until_complete(self.handle(msg))
            loop.close()

            if response_msg is None:
                return _make_response(self.name, message["sender"], message["task_type"],
                                      {}, status="done")
            return response_msg.to_dict()

        except RecoverableError as e:
            # Re-queue: set status back to pending so polling loop retries
            logger.warning(f"[{self.name}] RecoverableError — message will be re-queued: {e}")
            if self.db is not None:
                self.db.messages.update_one(
                    {"id": message.get("id")},
                    {"$set": {"status": "pending"}}
                )
            return None  # None signals the polling loop to skip writing a response

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            return _make_response(self.name, message["sender"], message["task_type"],
                                  {}, status="error", error=str(e))

    # ─── Async Run Loop (in-memory bus mode) ────────────────────────────────

    async def run(self, max_cycles: int = 100):
        """Async agent loop for in-memory bus mode."""
        self._running = True
        logger.info(f"[{self.name}] Agent loop started (max_cycles={max_cycles})")
        cycles = 0
        while self._running and cycles < max_cycles:
            msg = await self.receive(timeout=5.0)
            if msg:
                self.tokens.reset()  # fresh budget per task
                try:
                    response = await self.handle(msg)
                    if response:
                        await self.send(response)
                    self._tasks_completed += 1
                except RecoverableError as e:
                    logger.warning(f"[{self.name}] RecoverableError: {e} — re-queuing")
                    self._tasks_failed += 1
                except Exception as e:
                    logger.error(f"[{self.name}] Error handling {msg.task_type}: {e}")
                    error_reply = msg.reply({}, status="error", error=str(e))
                    await self.send(error_reply)
                    self._tasks_failed += 1
            cycles += 1
        self._running = False

    def stop(self):
        self._running = False

    @abstractmethod
    async def handle(self, message: AgentMessage) -> Optional[AgentMessage]:
        pass

    # ─── Status ─────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "agent": self.name,
            "running": self._running,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "total_tokens_used": self._total_tokens,
            "total_cost_usd": round(self._total_cost_usd, 6),
            "token_budget_remaining": dict(self.tokens.budgets),
            "success_rate_pct": round(
                self._tasks_completed / max(1, self._tasks_completed + self._tasks_failed) * 100, 1
            ),
        }


# ---------------------------------------------------------------------------
# Helper — mirrors Engineering agent's make_response()
# ---------------------------------------------------------------------------

def _make_response(sender, recipient, task_type, payload, status="done", error=None):
    return {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender": sender,
        "recipient": recipient,
        "task_type": task_type,
        "context": {},
        "payload": payload,
        "status": status,
        "error": error or "",
        "token_usage": {},
    }

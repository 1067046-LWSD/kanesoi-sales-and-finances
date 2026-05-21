"""
schema.py — Mandatory inter-agent message envelope (Group 5: Finance + Sales)
All agents must send/receive using this exact schema.
token_usage is appended by the agent AFTER the LLM call completes.
"""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional
import json

Status = Literal["pending", "in_progress", "done", "error"]

TASK_TYPES_FINANCE = [
    "GENERATE_PL_REPORT",
    "BUDGET_ALERT",
    "BUDGET_APPROVAL",
    "CASH_FLOW_FORECAST",
    "REVENUE_LOG",
    "AUDIT_REPORT",
    "MONTE_CARLO_SIM",
]

TASK_TYPES_SALES = [
    "QUALIFY_LEAD",
    "GENERATE_PITCH",
    "CLOSE_DEAL",
    "UPSELL",
    "PIPELINE_REPORT",
    "DEMO_REQUEST",
]


@dataclass
class TokenUsage:
    """Tracks token consumption and cost for every LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = "claude-sonnet-4-20250514"

    # Pricing: Sonnet 4 = $3/M input, $15/M output (as of May 2026 — update if pricing changes)
    INPUT_COST_PER_TOKEN = 3.00 / 1_000_000
    OUTPUT_COST_PER_TOKEN = 15.00 / 1_000_000

    @classmethod
    def from_response(cls, response_usage: dict, model: str = "claude-sonnet-4-20250514") -> "TokenUsage":
        inp = response_usage.get("input_tokens", 0)
        out = response_usage.get("output_tokens", 0)
        cost = (inp * cls.INPUT_COST_PER_TOKEN) + (out * cls.OUTPUT_COST_PER_TOKEN)
        return cls(
            input_tokens=inp,
            output_tokens=out,
            total_tokens=inp + out,
            cost_usd=round(cost, 6),
            model=model,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentMessage:
    """
    Canonical message envelope — identical for all agents.
    Only `payload` is agent-specific.
    """
    task_type: str
    sender: str
    recipient: str
    payload: dict
    context: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: Status = "pending"
    error: str = ""
    token_usage: dict = field(default_factory=dict)  # filled after LLM call

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "recipient": self.recipient,
            "task_type": self.task_type,
            "context": self.context,
            "payload": self.payload,
            "status": self.status,
            "error": self.error,
            "token_usage": self.token_usage,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentMessage":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            sender=data["sender"],
            recipient=data["recipient"],
            task_type=data["task_type"],
            context=data.get("context", {}),
            payload=data.get("payload", {}),
            status=data.get("status", "pending"),
            error=data.get("error", ""),
            token_usage=data.get("token_usage", {}),
        )

    def reply(self, payload: dict, status: Status = "done", error: str = "") -> "AgentMessage":
        """Create a response message swapping sender/recipient."""
        return AgentMessage(
            task_type=self.task_type,
            sender=self.recipient,
            recipient=self.sender,
            payload=payload,
            context=self.context,
            status=status,
            error=error,
        )

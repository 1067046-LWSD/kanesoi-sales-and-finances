"""
bus/message_bus.py — Lightweight async in-memory message bus.
Agents communicate asynchronously via this shared queue.
Redis can replace this for production; interface stays identical.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Awaitable

logger = logging.getLogger("message_bus")

# Global cumulative token/cost tracker across ALL agents in this session
_SESSION_STATS = {
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_tokens": 0,
    "total_cost_usd": 0.0,
    "calls": 0,
    "messages_sent": 0,
}


class MessageBus:
    """
    Async in-memory publish/subscribe message bus.
    Agents subscribe to their own name and receive messages routed to them.
    Supports broadcast (recipient = "broadcast") to all subscribers.
    """

    def __init__(self):
        # agent_name -> asyncio.Queue
        self._queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._log: list[dict] = []  # full audit log of all messages
        self._subscribers: set[str] = set()

    def register(self, agent_name: str):
        """Register an agent so it can receive messages."""
        self._queues[agent_name]  # touch to create
        self._subscribers.add(agent_name)
        logger.info(f"[BUS] Registered agent: {agent_name}")

    async def send(self, message: dict):
        """Route a message to its recipient(s)."""
        recipient = message.get("recipient", "")
        self._log.append({**message, "_bus_routed_at": datetime.now(timezone.utc).isoformat()})
        _SESSION_STATS["messages_sent"] += 1

        # Update session token stats if token_usage present
        tu = message.get("token_usage", {})
        if tu:
            _SESSION_STATS["total_input_tokens"] += tu.get("input_tokens", 0)
            _SESSION_STATS["total_output_tokens"] += tu.get("output_tokens", 0)
            _SESSION_STATS["total_tokens"] += tu.get("total_tokens", 0)
            _SESSION_STATS["total_cost_usd"] += tu.get("cost_usd", 0.0)
            _SESSION_STATS["calls"] += 1

        if recipient == "broadcast":
            for name, q in self._queues.items():
                await q.put(message)
            logger.info(f"[BUS] Broadcast from {message['sender']} → all ({len(self._queues)} agents)")
        elif recipient in self._queues:
            await self._queues[recipient].put(message)
            logger.info(f"[BUS] Routed {message['task_type']} from {message['sender']} → {recipient}")
        else:
            logger.warning(f"[BUS] Unknown recipient '{recipient}' — message dropped")

    async def receive(self, agent_name: str, timeout: float = 30.0) -> dict | None:
        """Block until a message arrives for this agent (or timeout)."""
        try:
            return await asyncio.wait_for(self._queues[agent_name].get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def peek(self, agent_name: str) -> list[dict]:
        """Non-blocking: return all queued messages without consuming them."""
        q = self._queues.get(agent_name, asyncio.Queue())
        items = []
        while not q.empty():
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def get_log(self) -> list[dict]:
        return list(self._log)

    def get_session_stats(self) -> dict:
        return dict(_SESSION_STATS)

    def dump_log(self, path: str):
        with open(path, "w") as f:
            json.dump(self._log, f, indent=2)
        logger.info(f"[BUS] Log dumped to {path}")


# Singleton bus — import this everywhere
bus = MessageBus()

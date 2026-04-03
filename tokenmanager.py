"""
token_manager.py
─────────────────────────────────────────────────────────────────────────────
Central token budget and usage tracker for every agent in the Kanosei system.

How it works
────────────
1.  Each agent gets a MONTHLY_BUDGET (tokens/month).
2.  Each task_type has a COST_ESTIMATE (expected tokens to complete the task).
    LLM-heavy tasks (e.g. GENERATE_PITCH) cost more than pure-logic tasks.
3.  Before an agent handles a task, it calls ⁠ check_and_reserve() ⁠.
    • If the agent has enough headroom → tokens are reserved, task proceeds.
    • If not → the task is rejected and a BUDGET_EXCEEDED message is returned.
4.  After the task finishes, ⁠ commit() ⁠ is called with the actual token count.
    The difference between reserved and actual is refunded automatically.
5.  ⁠ report() ⁠ dumps a full usage snapshot in the shared JSON envelope so the
    CEO agent can monitor spend in real time.

Token cost rationale
────────────────────
Finance tasks are mostly deterministic lookups and arithmetic → low cost.
Sales tasks involve LLM calls (pitch generation, objection handling) → higher.

  Task                  Estimate   Why
  ────────────────────  ────────   ──────────────────────────────────────
  APPROVE_SPEND           500      Simple threshold check + one message out
  LOG_EXPENSE             300      DB write + ack
  GENERATE_PL             800      Aggregate query + formatted report
  BURN_RATE_ALERT         600      Math + conditional warning text
  ALLOCATE_BUDGET         300      DB write + ack
  VALIDATE_REVENUE        500      Cross-check + conditional branch
  ────────────────────  ────────
  QUALIFY_LEAD            700      Scoring logic + DB write
  GENERATE_PITCH         2500      Full LLM call for personalised copy
  LOG_REVENUE             400      DB write + two outbound messages
  PIPELINE_REPORT        1200      Aggregate query + rich summary
  REVENUE_FORECAST       1800      LLM-assisted probability estimates
  CUSTOMER_FEEDBACK       500      Forward + ack
  ESCALATE_DISCOUNT       500      Threshold check + conditional message

Monthly budgets
───────────────
  Finance Agent   50 000 tokens   Mostly rule-based; minimal LLM
  Sales Agent    200 000 tokens   Heavy LLM use for outreach & forecasting

Alert thresholds
────────────────
  WARNING   80 % of budget consumed  → send BUDGET_WARNING to CEO
  CRITICAL  95 % of budget consumed  → send BUDGET_CRITICAL to CEO
"""

import uuid
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional


# ── Budget & cost tables ──────────────────────────────────────────────────────

MONTHLY_BUDGETS: dict[str, int] = {
    "Finance": 50_000,
    "Sales":  200_000,
}

# Estimated tokens per task (input + output combined).
# Used to pre-flight check before the task starts.
TASK_COST_ESTIMATES: dict[str, int] = {
    # Finance tasks
    "APPROVE_SPEND":     500,
    "LOG_EXPENSE":       300,
    "GENERATE_PL":       800,
    "BURN_RATE_ALERT":   600,
    "ALLOCATE_BUDGET":   300,
    "VALIDATE_REVENUE":  500,

    # Sales tasks
    "QUALIFY_LEAD":      700,
    "GENERATE_PITCH":   2_500,
    "LOG_REVENUE":       400,
    "PIPELINE_REPORT":  1_200,
    "REVENUE_FORECAST": 1_800,
    "CUSTOMER_FEEDBACK": 500,
    "ESCALATE_DISCOUNT": 500,
}

# Alert thresholds (fraction of budget consumed)
WARN_AT     = 0.80   # 80 %  → WARNING
CRITICAL_AT = 0.95   # 95 %  → CRITICAL

# Default fallback estimate if task_type isn't in the table above
DEFAULT_COST_ESTIMATE = 600


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TaskRecord:
    """One completed (or in-progress) task."""
    task_id:    str
    agent:      str
    task_type:  str
    reserved:   int          # tokens set aside before the task ran
    actual:     int  = 0     # tokens truly consumed (filled in after)
    committed:  bool = False
    timestamp:  str  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AgentTokenState:
    """Running totals for one agent."""
    agent:          str
    monthly_budget: int
    used:           int  = 0   # committed actual usage
    reserved:       int  = 0   # pending reservations not yet committed
    warn_sent:      bool = False
    critical_sent:  bool = False

    @property
    def available(self) -> int:
        return self.monthly_budget - self.used - self.reserved

    @property
    def utilisation(self) -> float:
        return (self.used + self.reserved) / self.monthly_budget


# ── TokenManager ─────────────────────────────────────────────────────────────

class TokenManager:
    """
    Singleton-style manager – create one instance and share it across agents.

    Usage
    ─────
        tm = TokenManager()

        # Before the agent handles the task:
        ok, reservation_id, msg = tm.check_and_reserve("Finance", "APPROVE_SPEND")
        if not ok:
            # send BUDGET_EXCEEDED back to caller
            return msg

        # ... agent does its work, gets actual token count back from API ...
        actual_tokens = api_response["usage"]["total_tokens"]

        # After the task:
        alerts = tm.commit(reservation_id, actual_tokens)
        for alert in alerts:
            send_message("CEO", alert["task_type"], alert["payload"])
    """

    def _init_(self):
        self._states: dict[str, AgentTokenState] = {
            agent: AgentTokenState(agent=agent, monthly_budget=budget)
            for agent, budget in MONTHLY_BUDGETS.items()
        }
        self._records: dict[str, TaskRecord] = {}   # reservation_id → TaskRecord

    # ── Public API ────────────────────────────────────────────────────────────

    def check_and_reserve(
        self,
        agent: str,
        task_type: str,
        override_estimate: Optional[int] = None,
    ) -> tuple[bool, str, dict]:
        """
        Attempt to reserve tokens for an upcoming task.

        Returns
        ───────
        (True,  reservation_id, {})          – approved, proceed
        (False, "",             error_msg)   – rejected, send error_msg to caller
        """
        state = self._get_or_create_state(agent)
        estimate = override_estimate or TASK_COST_ESTIMATES.get(task_type, DEFAULT_COST_ESTIMATE)

        if estimate > state.available:
            error = _budget_exceeded_message(agent, task_type, estimate, state)
            return False, "", error

        # Reserve the tokens
        reservation_id = str(uuid.uuid4())
        record = TaskRecord(
            task_id=reservation_id,
            agent=agent,
            task_type=task_type,
            reserved=estimate,
        )
        self._records[reservation_id] = record
        state.reserved += estimate

        return True, reservation_id, {}

    def commit(self, reservation_id: str, actual_tokens: int) -> list[dict]:
        """
        Finalise a reservation with the true token count.
        Returns a (possibly empty) list of alert messages to forward to CEO.
        """
        record = self._records.get(reservation_id)
        if record is None or record.committed:
            return []

        state = self._get_or_create_state(record.agent)

        # Release the reservation and apply actual usage
        state.reserved = max(0, state.reserved - record.reserved)
        state.used     += actual_tokens

        record.actual    = actual_tokens
        record.committed = True

        return self._check_alerts(state)

    def report(self, agent: str) -> dict:
        """
        Return a full token-usage snapshot for one agent.
        Suitable for dropping into a message payload.
        """
        state = self._get_or_create_state(agent)
        return {
            "agent":          agent,
            "monthly_budget": state.monthly_budget,
            "used":           state.used,
            "reserved":       state.reserved,
            "available":      state.available,
            "utilisation_pct": round(state.utilisation * 100, 1),
            "snapshot_at":    datetime.now(timezone.utc).isoformat(),
            "task_log":       self._task_log_for(agent),
        }

    def full_report(self) -> dict:
        """Usage snapshot for all agents."""
        return {
            agent: self.report(agent)
            for agent in self._states
        }

    def reset_month(self, agent: Optional[str] = None):
        """Reset monthly counters (call at start of each billing period)."""
        targets = [agent] if agent else list(self._states)
        for a in targets:
            s = self._states.get(a)
            if s:
                s.used        = 0
                s.reserved    = 0
                s.warn_sent   = False
                s.critical_sent = False
        self._records.clear()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_or_create_state(self, agent: str) -> AgentTokenState:
        if agent not in self._states:
            # Unknown agent: give a sensible default budget
            self._states[agent] = AgentTokenState(
                agent=agent,
                monthly_budget=50_000,
            )
        return self._states[agent]

    def _check_alerts(self, state: AgentTokenState) -> list[dict]:
        alerts = []
        u = state.utilisation

        if u >= CRITICAL_AT and not state.critical_sent:
            state.critical_sent = True
            alerts.append(_alert_message(state, level="CRITICAL"))

        elif u >= WARN_AT and not state.warn_sent:
            state.warn_sent = True
            alerts.append(_alert_message(state, level="WARNING"))

        return alerts

    def _task_log_for(self, agent: str) -> list[dict]:
        return [
            {
                "task_id":   r.task_id,
                "task_type": r.task_type,
                "reserved":  r.reserved,
                "actual":    r.actual,
                "committed": r.committed,
                "timestamp": r.timestamp,
            }
            for r in self._records.values()
            if r.agent == agent
        ]


# ── Message builders ──────────────────────────────────────────────────────────

def _budget_exceeded_message(agent: str, task_type: str, needed: int, state: AgentTokenState) -> dict:
    return {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    agent,
        "recipient": "CEO",
        "task_type": "BUDGET_EXCEEDED",
        "context":   {},
        "payload": {
            "agent":          agent,
            "blocked_task":   task_type,
            "tokens_needed":  needed,
            "tokens_available": state.available,
            "monthly_budget": state.monthly_budget,
            "used":           state.used,
            "message": (
                f"{agent} agent cannot run {task_type}: "
                f"needs {needed:,} tokens but only {state.available:,} remain "
                f"of the {state.monthly_budget:,} monthly budget."
            ),
        },
        "status": "error",
        "error":  "INSUFFICIENT_TOKEN_BUDGET",
    }


def _alert_message(state: AgentTokenState, level: str) -> dict:
    pct = round(state.utilisation * 100, 1)
    task_type = f"BUDGET_{level}"   # BUDGET_WARNING or BUDGET_CRITICAL
    return {
        "id":        str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender":    state.agent,
        "recipient": "CEO",
        "task_type": task_type,
        "context":   {},
        "payload": {
            "agent":           state.agent,
            "level":           level,
            "utilisation_pct": pct,
            "used":            state.used,
            "monthly_budget":  state.monthly_budget,
            "available":       state.available,
            "message": (
                f"[{level}] {state.agent} agent has consumed {pct}% of its "
                f"monthly token budget ({state.used:,} / {state.monthly_budget:,})."
            ),
        },
        "status": "done",
        "error":  "",
    }


# ── Convenience decorator ─────────────────────────────────────────────────────

def token_tracked(agent_name: str, tm: TokenManager):
    """
    Decorator that wraps a task-handler function with automatic token
    reservation and commit.

    The wrapped function must accept ⁠ message ⁠ as its first argument and
    return a dict with an optional key ⁠ "actual_tokens" ⁠ (int).  If absent,
    the estimate is committed as the actual cost.

    Example
    ───────
        @token_tracked("Finance", token_manager)
        def handle_approve_spend(message):
            ...
            return {"result": ..., "actual_tokens": 420}
    """
    def decorator(fn):
        def wrapper(message, *args, **kwargs):
            task_type = message.get("task_type", "UNKNOWN")
            ok, reservation_id, err = tm.check_and_reserve(agent_name, task_type)

            if not ok:
                print(json.dumps(err, indent=2))
                return err  # caller should forward this to CEO

            result = fn(message, *args, **kwargs)

            actual = result.pop("actual_tokens", None) if isinstance(result, dict) else None
            if actual is None:
                # Fall back to the estimate
                record = tm._records.get(reservation_id)
                actual = record.reserved if record else DEFAULT_COST_ESTIMATE

            alerts = tm.commit(reservation_id, actual)
            for alert in alerts:
                print(f"\n[TokenManager → CEO] {alert['task_type']}")
                print(json.dumps(alert, indent=2))

            return result
        return wrapper
    return decorator


# ── Quick self-test ───────────────────────────────────────────────────────────

if _name_ == "_main_":
    tm = TokenManager()

    print("=== check_and_reserve: Finance / APPROVE_SPEND ===")
    ok, rid, err = tm.check_and_reserve("Finance", "APPROVE_SPEND")
    print(f"  approved={ok}  reservation_id={rid}")

    print("\n=== commit with actual 480 tokens ===")
    alerts = tm.commit(rid, 480)
    print(f"  alerts generated: {len(alerts)}")

    print("\n=== Finance token report ===")
    print(json.dumps(tm.report("Finance"), indent=2))

    print("\n=== Exhaust Sales budget to trigger BUDGET_WARNING ===")
    # Burn 85 % of the Sales budget in one shot to trigger a warning
    ok2, rid2, _ = tm.check_and_reserve("Sales", "GENERATE_PITCH", override_estimate=170_000)
    alerts2 = tm.commit(rid2, 170_000)
    for a in alerts2:
        print(f"  Alert: {a['task_type']} — {a['payload']['message']}")

    print("\n=== Full system report ===")
    print(json.dumps(tm.full_report(), indent=2))

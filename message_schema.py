import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

def new_id():
    return f"req-{str(uuid.uuid4())[:8]}"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

@dataclass
class AgentMessage:
    sender: str
    recipient: str
    task_type: str
    payload: dict
    context: dict = field(default_factory=dict)
    status: str = "pending"
    id: str = field(default_factory=new_id)
    timestamp: str = field(default_factory=now_iso)
    error: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

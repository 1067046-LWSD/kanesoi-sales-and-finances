Framework
We chose CrewAI as our framework. A framework is basically a set of pre-built tools and rules that helps organize how everything works together. CrewAI was the best fit because it's already built around roles and tasks, which is exactly how our system is set up: a CEO agent that hands off work to Finance, Sales, and other agents.
We also looked at a few other options. Plain Python gives the most control but we'd have to build everything from scratch. AutoGen is good for back-and-forth conversations but isn't really role-focused. LangGraph is powerful but way more complex than we need right now.
Tools
Finance Agent tools: a budget calculator, a SQLite database for storing expenses and revenue, a profit/loss report builder, a cash flow simulator that projects runway for the next 3-6 months, and an alert system for burn rate warnings.
Sales Agent tools: a lead scorer, an LLM-powered message writer for personalized outreach, a SQLite database to track leads and deals, a tool that sends closed deal info to the Finance agent, and an objection handler that matches common pushbacks to prepared responses and escalates if needed.
Agent API
Both agents are built the same way so the CEO can talk to either one using the exact same approach. Every agent can do three things: receive a message and figure out what kind of task it is, handle that task using whatever tools it needs and return a result, and send the result back wrapped in that standard message format.
Finance and Sales each have their own specific task handlers, but they all plug into that same three-step system.

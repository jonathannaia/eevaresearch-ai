"""The ten MCP tools (design §6), one module per tool, each a thin typed
wrapper over existing adapter/repository functions. Every tool takes a
ToolContext (the per-session authorization scope, budget, evidence
registry, and audit sink — see _context.py) as its first argument and
returns a typed result from src.mcp_agent.contracts; none raises into
the caller, none performs a live fetch that the existing cache-first
service functions would not have performed for a human-triggered view,
and none writes anything except the two policy-mediated request tools.
"""

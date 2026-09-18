"""Autonomous Evidence-First Research Agent — MCP tool surface and session
wiring (design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md).

One agent, one workflow. This package is the ONLY way the agent touches
EevaResearch data: ten narrow, typed tools (§6) that wrap existing adapter/
repository functions — never a reimplementation, never a write path
except the two policy-mediated request tools (save_private_research_packet,
request_publication_decision), and even those never decide publication;
that decision lives in src.logic.publication_policy, outside this package.

Hard boundaries, enforced by tests/test_mcp_agent_tool_scope_guard.py
(AST-based, modeled on tests/test_company_discovery_scope_guard.py):
- nothing here imports src.data_access.company_discovery (§2.7, §8.4);
- nothing here imports a write-capable pipeline entry point
  (radar_pipeline/edgar_pipeline/edinet_pipeline process_candidate/run_*);
- nothing here opens a raw HTTP client, shell, filesystem walk, or SQL
  cursor — tools call existing service functions only.
"""

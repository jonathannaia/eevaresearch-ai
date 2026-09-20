"""Agent Review — the window onto what the autonomous agent decided, and
what it was allowed to do about it.

Hidden and doubly gated. Two checks run before this page constructs a
repository or reads a single row: EDGE_AGENT_REVIEW_PAGE_ENABLED must be
on, and is_admin() must be true. A signed-out visitor, a signed-in
non-admin, or anyone arriving by a direct URL on a deployment where the
flag is off sees one generic line and triggers no query at all. The
page's absence from every nav list is a convenience, not the boundary.

Strictly read-only. There is no button, form or callback here that
changes anything: no approve, no publish, no re-run, no mode control.
The only way to restrict the agent is scripts/agent_control.py, and the
only way to widen it is a deploy.

The central distinction this page exists to make is between what the
policy WOULD have done and what actually happened. In shadow mode those
are always different, and collapsing them would be the one mistake that
makes the whole release pointless — a reader has to be able to see
"this would have been published" and "nothing was published" at the
same time, along with which hold was responsible.

It imports no agent runtime. src/logic/agent_health.py and
src/logic/agent_mode.py are pure, and the data comes from the agent
repositories through backend_factory; the scheduler and the publication
policy reach src.mcp_agent transitively and are therefore never imported
here (tests/test_agent_review_scope_guard.py).
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone

import streamlit as st

from src.config.settings import get_settings
from src.logic import agent_health, agent_mode
from src.ui.components.section import section_header
from src.ui.ui import is_admin

PAGE_SIZE = 25
ACCESS_DENIED = "Access denied."

# Filter keys, which are also the query-parameter names — one spelling,
# so a shared link and the widget state can never drift apart.
QP_POLICY = "policy"
QP_EFFECTIVE = "effective"
QP_CANDIDATE_STATUS = "cstatus"
QP_FROM = "from"
QP_TO = "to"
QP_ISSUER = "issuer"
QP_EVENT = "event"
QP_BLOCKED = "blocked"
QP_PAGE = "page"
QP_PACKET = "packet"

ANY = "Any"


def _esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


def _chip(label: str, value: str, *, strong: bool = False) -> str:
    """Neutral surface tokens only. The evidence palette
    (--ev-supports/--ev-contradicts) means "this evidence supports or
    contradicts a claim" and must never be borrowed to colour a generic
    UI status, or the two readings start contaminating each other."""
    weight = "600" if strong else "500"
    return (
        '<span style="display:inline-flex;gap:var(--space-2);align-items:baseline;'
        "background:var(--surface-chip);border:1px solid var(--border-subtle);"
        "border-radius:var(--r-sm);padding:var(--space-1) var(--space-3);margin:0 var(--space-2) var(--space-2) 0;\">"
        f'<span style="color:var(--text-label);font-size:var(--fs-label);text-transform:uppercase;'
        f'letter-spacing:.04em;">{_esc(label)}</span>'
        f'<span style="color:var(--text);font-weight:{weight};">{_esc(value)}</span></span>'
    )


def _qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:  # noqa: BLE001 — no browser context (bare/test runs)
        return {}


def _set_qp(values: dict[str, str]) -> None:
    try:
        for key, value in values.items():
            if value:
                st.query_params[key] = value
            elif key in st.query_params:
                del st.query_params[key]
    except Exception:  # noqa: BLE001
        return


def _pick(params: dict, key: str, options: list[str]) -> str:
    value = params.get(key)
    value = value[0] if isinstance(value, list) else value
    return value if value in options else ANY


def _int(params: dict, key: str, default: int = 1) -> int:
    value = params.get(key)
    value = value[0] if isinstance(value, list) else value
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _selected(value: str) -> str | None:
    return None if value == ANY else value


# --- the page ----------------------------------------------------------------

def render() -> None:
    st.markdown('<div class="er-page-title">Agent Review</div>', unsafe_allow_html=True)

    # Gate one: the deployment flag. Gate two: the signed-in admin. Both
    # run before any repository is constructed and any row is read.
    settings = get_settings()
    if not settings.agent_review_page_enabled:
        st.info("Agent Review is not enabled on this deployment.")
        return
    if not is_admin(settings):
        st.error(ACCESS_DENIED)
        return

    from src.data_access import backend_factory  # deferred: nothing is built until both gates pass

    try:
        store = backend_factory.get_agent_store_repository(settings)
    except Exception as exc:  # noqa: BLE001 — never leak a connection string into the UI
        st.info(f"Could not read the agent store ({type(exc).__name__}).")
        return

    try:
        _render_body(settings, store)
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


def _render_body(settings, store) -> None:
    st.markdown(
        '<div class="er-muted">Read-only. Nothing on this page publishes, approves, re-runs or '
        "changes the agent's mode.</div>",
        unsafe_allow_html=True,
    )

    now = datetime.now(timezone.utc)
    resolved = _resolve_mode(settings, store)
    health = agent_health.build_health(store, resolved, now=now, since=(now - timedelta(hours=24)).isoformat())
    _render_health(health)

    params = _qp()
    packet_id = params.get(QP_PACKET)
    packet_id = packet_id[0] if isinstance(packet_id, list) else packet_id
    if packet_id:
        _render_detail(store, packet_id)
        return
    _render_list(settings, store, params)


def _resolve_mode(settings, store):
    try:
        control = store.get_control()
    except Exception:  # noqa: BLE001 — an unreadable control row never widens anything
        control = None
    return agent_mode.resolve_mode(settings, control)


# --- health ---------------------------------------------------------------------

def _render_health(health: agent_health.AgentHealth) -> None:
    section_header("Health")

    if health.heartbeat_is_stale:
        st.warning(
            f"The last run is still marked running but its heartbeat is "
            f"{agent_health.humanize_age(health.heartbeat_age_minutes)} "
            f"(stale after {agent_health.HEARTBEAT_STALE_MINUTES} min). The worker may have died without "
            "closing its run; the next worker to start will reclaim it."
        )
    if health.has_job_trouble:
        st.error(
            f"{health.dead_jobs} dead and {health.failed_jobs} failed job(s) in the last 24h "
            f"({health.error_rate:.0%} of finished jobs). Check the decision reasons below."
        )
    if health.kill_switch_on:
        st.warning("The publication kill switch is on: every decision is held regardless of policy outcome.")
    if health.override_mode:
        st.warning(
            f"An emergency override is in force — mode {health.override_mode!r}"
            + (f", reason: {health.override_reason}" if health.override_reason else "")
            + (f" (set by {health.override_by})" if health.override_by else "")
        )

    chips = [
        _chip("Configured", health.configured_mode),
        _chip("Effective", health.effective_label, strong=True),
        _chip("Kill switch", "on" if health.kill_switch_on else "off"),
        _chip("Override", health.override_mode or "none"),
        _chip("Last run", health.last_run_status or "never"),
        _chip("Heartbeat", agent_health.humanize_age(health.heartbeat_age_minutes)),
        _chip("Packets 24h", str(health.packets_24h)),
    ]
    for state in ("pending", "leased", "done", "failed", "dead"):
        count = (health.jobs_24h or {}).get(state, 0)
        if count:
            chips.append(_chip(f"Jobs {state}", str(count)))
    for decision, count in sorted((health.decisions_24h or {}).items()):
        chips.append(_chip(f"24h {decision}", str(count)))
    st.markdown(f'<div style="margin:var(--space-3) 0;">{"".join(chips)}</div>', unsafe_allow_html=True)

    if health.was_downgraded:
        st.caption(
            f"Configured mode is {health.configured_mode}; the effective mode is {health.effective_mode}. "
            "Publishing is not part of this release."
        )
    if health.last_run_id:
        st.caption(
            f"Run {health.last_run_id} · started {health.last_run_started_at or '—'} · "
            f"completed {health.last_run_completed_at or '—'}"
        )


# --- list ------------------------------------------------------------------------

def _candidates_by_id(settings, sources: list[str]) -> dict[str, object]:
    """Read-only candidate context. The agent tables carry no candidate
    status, so the status filter resolves matching candidate ids here and
    passes them into the agent query — which keeps pagination correct and
    keeps the decision list itself an agent-tables-only read."""
    from src.data_access import backend_factory

    out: dict[str, object] = {}
    for source in sources:
        try:
            repo = backend_factory.get_candidate_repository(settings, source)
            out.update(repo.load_candidates())
        except Exception:  # noqa: BLE001 — candidate context is a nicety, never a blocker
            continue
    return out


def _render_list(settings, store, params: dict) -> None:
    section_header("Decisions")

    try:
        issuers = [ANY, *store.distinct_issuers()]
        events = [ANY, *store.distinct_event_types()]
    except Exception:  # noqa: BLE001
        issuers, events = [ANY], [ANY]

    policy_options = [ANY, "AUTO_PUBLISHED", "VERIFIED_DRAFT", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE",
                      "NOT_MATERIAL", "DUPLICATE", "FAILED_RETRIEVAL"]
    effective_options = [ANY, "NO_ACTION", "AUTO_PUBLISHED", "VERIFIED_DRAFT", "REVIEW_REQUIRED"]
    status_options = [ANY, "NEEDS_REVIEW", "PUBLISHED", "DISMISSED", "VERIFIED_DRAFT"]

    row1 = st.columns(4)
    policy = row1[0].selectbox("Policy decision (would have)", policy_options,
                               index=policy_options.index(_pick(params, QP_POLICY, policy_options)))
    effective = row1[1].selectbox("Actual action", effective_options,
                                  index=effective_options.index(_pick(params, QP_EFFECTIVE, effective_options)))
    candidate_status = row1[2].selectbox("Candidate status", status_options,
                                         index=status_options.index(_pick(params, QP_CANDIDATE_STATUS, status_options)))
    issuer = row1[3].selectbox("Issuer / company", issuers, index=issuers.index(_pick(params, QP_ISSUER, issuers)))

    row2 = st.columns(4)
    date_from = row2[0].text_input("Decided from (YYYY-MM-DD)", value=str(params.get(QP_FROM, "") or ""))
    date_to = row2[1].text_input("Decided to (YYYY-MM-DD)", value=str(params.get(QP_TO, "") or ""))
    event = row2[2].selectbox("Event type", events, index=events.index(_pick(params, QP_EVENT, events)))
    blocked_only = row2[3].checkbox("Publication blocked only",
                                    value=str(params.get(QP_BLOCKED, "")).lower() in ("1", "true"))

    page = _int(params, QP_PAGE, 1)

    candidate_ids = None
    candidates = {}
    if candidate_status != ANY:
        candidates = _candidates_by_id(settings, ["SEC EDGAR", "OpenDART / DART", "EDINET"])
        candidate_ids = [
            cid for cid, candidate in candidates.items()
            if getattr(getattr(candidate, "status", None), "value", None) == candidate_status
        ]

    filters = dict(
        policy_decision=_selected(policy), effective_decision=_selected(effective),
        issuer_id=_selected(issuer), event_type=_selected(event), blocked_only=bool(blocked_only),
        decided_from=(date_from.strip() or None), decided_to=(date_to.strip() + "T23:59:59+00:00") if date_to.strip() else None,
        candidate_ids=candidate_ids,
    )

    try:
        total = store.count_matching_decisions(**filters)
        rows = store.list_decisions(**filters, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    except Exception as exc:  # noqa: BLE001
        st.info(f"Could not read the decision list ({type(exc).__name__}).")
        return

    _set_qp({
        QP_POLICY: "" if policy == ANY else policy,
        QP_EFFECTIVE: "" if effective == ANY else effective,
        QP_CANDIDATE_STATUS: "" if candidate_status == ANY else candidate_status,
        QP_ISSUER: "" if issuer == ANY else issuer,
        QP_EVENT: "" if event == ANY else event,
        QP_FROM: date_from.strip(), QP_TO: date_to.strip(),
        QP_BLOCKED: "1" if blocked_only else "",
        QP_PAGE: "" if page == 1 else str(page),
    })

    if total == 0:
        st.info("No agent decisions match these filters yet.")
        return

    last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    st.caption(f"{total} decision(s) · page {min(page, last_page)} of {last_page}")

    for row in rows:
        _render_list_row(row)

    nav = st.columns(2)
    if page > 1:
        nav[0].link_button("← Previous", _page_link(params, page - 1))
    if page < last_page:
        nav[1].link_button("Next →", _page_link(params, page + 1))


def _page_link(params: dict, page: int) -> str:
    keep = {k: v for k, v in params.items() if k not in (QP_PAGE, QP_PACKET) and v}
    keep[QP_PAGE] = str(page)
    return "?" + "&".join(f"{k}={html.escape(str(v))}" for k, v in keep.items())


def _render_list_row(row) -> None:
    held = row.blocked_by or "not held"
    st.markdown(
        '<div style="border:1px solid var(--border);border-radius:var(--r-md);padding:var(--space-4);'
        'margin-bottom:var(--space-3);background:var(--surface);">'
        f'<div style="color:var(--text-muted);font-size:var(--fs-label);">{_esc(row.decided_at)} · '
        f"{_esc(row.source)} · {_esc(row.issuer_id or 'unresolved issuer')}</div>"
        f'<div style="color:var(--text);font-weight:600;margin:var(--space-2) 0;">{_esc(row.candidate_id)}</div>'
        + _chip("Policy decision (would have)", row.policy_decision, strong=True)
        + _chip("Actual action", row.effective_decision, strong=True)
        + _chip("Publication held by", held)
        + _chip("Mode", row.mode)
        + _chip("Quote verified", "yes" if row.quote_verified else "no")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.link_button("Open decision", f"?{QP_PACKET}={row.packet_id}")


# --- detail ------------------------------------------------------------------------

def _render_detail(store, packet_id: str) -> None:
    try:
        packet = store.get_packet(packet_id)
        decision = store.get_decision(packet_id)
        events = store.audit_events_for_packet(packet_id)
    except Exception as exc:  # noqa: BLE001
        st.info(f"Could not read this decision ({type(exc).__name__}).")
        return

    st.link_button("← Back to all decisions", "?")
    if packet is None or decision is None:
        st.info("That decision no longer exists.")
        return

    section_header("Decision")
    st.markdown(
        _chip("Policy decision (would have)", decision.policy_decision, strong=True)
        + _chip("Actual action", decision.effective_decision, strong=True)
        + _chip("Publication held by", decision.blocked_by or "not held")
        + _chip("Mode", decision.mode)
        + _chip("Kill switch", "on" if decision.kill_switch_on else "off")
        + _chip("Quote verified", "yes" if decision.quote_verified else "no")
        + _chip("Candidate status written", "yes" if decision.candidate_status_written else "no")
        + _chip("Published", "yes" if decision.published else "no"),
        unsafe_allow_html=True,
    )
    st.caption(
        f"Policy version {decision.policy_version} · decided {decision.decided_at} · "
        f"packet created {packet.created_at}"
    )

    section_header("Rationale")
    for reason in _loads(decision.reasons_json, []):
        st.markdown(f"- {_esc(reason)}", unsafe_allow_html=True)

    section_header("Policy rows 0–11")
    rows = _loads(decision.row_results_json, [])
    if not rows:
        st.write("No row results were recorded.")
    for entry in rows:
        number, condition, passed = (list(entry) + [None, None, None])[:3]
        detail = entry[3] if isinstance(entry, list) and len(entry) > 3 else ""
        mark = "PASS" if passed else "FAIL"
        # The verdict sits in its own fixed-width, right-aligned column so
        # every row's PASS/FAIL lines up down the page — a row that fails
        # also carries a detail string, and letting that push the verdict
        # sideways is exactly what makes a failing row hard to spot.
        st.markdown(
            f'<div style="display:flex;gap:var(--space-3);align-items:baseline;padding:var(--space-2) 0;'
            'border-bottom:1px solid var(--border-subtle);">'
            f'<span style="color:var(--text-muted);min-width:3.5rem;font-family:var(--font-mono);">'
            f"Row {_esc(number)}</span>"
            f'<span style="color:var(--text);flex:1;">{_esc(condition)}</span>'
            + (f'<span style="color:var(--text-muted);flex:1;">{_esc(detail)}</span>'
               if detail else '<span style="flex:1;"></span>')
            + f'<span style="color:var(--text-label);font-weight:600;min-width:3rem;text-align:right;">{mark}</span>'
            + "</div>",
            unsafe_allow_html=True,
        )

    section_header("Original filing / source")
    st.markdown(
        _chip("Source", packet.source) + _chip("Seed document", packet.seed_document_id)
        + _chip("Candidate", packet.candidate_id) + _chip("Session", packet.session_id),
        unsafe_allow_html=True,
    )

    section_header("Issuer resolution")
    resolution = _loads(packet.issuer_resolution_json, None)
    if isinstance(resolution, dict) and resolution:
        # Chips rather than a collapsed JSON blob: how confidently the
        # issuer resolved is one of the first things a reviewer checks,
        # and it should not be behind a disclosure triangle.
        st.markdown(
            "".join(
                _chip(key.replace("_", " "), value, strong=(key == "resolution_confidence"))
                for key, value in resolution.items() if value not in (None, "", [], {})
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(_chip("Issuer", packet.issuer_id or "unresolved"), unsafe_allow_html=True)

    section_header("Claims")
    proposal = _loads(packet.proposal_json, {}) or {}
    for claim in proposal.get("claims", []):
        st.markdown(
            '<div style="border:1px solid var(--border);border-radius:var(--r-md);padding:var(--space-4);'
            'margin-bottom:var(--space-3);background:var(--surface);">'
            f'<div style="color:var(--text);font-weight:600;">{_esc(claim.get("headline"))}</div>'
            f'<div style="color:var(--text-secondary);margin:var(--space-2) 0;">{_esc(claim.get("statement"))}</div>'
            f'<div style="color:var(--text-muted);font-size:var(--fs-label);">What this does not establish: '
            f'{_esc(claim.get("what_this_does_not_establish"))}</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    section_header("Stored evidence and excerpt")
    for item in packet.evidence:
        st.markdown(
            '<div style="border:1px solid var(--border-subtle);border-radius:var(--r-sm);'
            'padding:var(--space-3);margin-bottom:var(--space-2);background:var(--surface-chip);">'
            f'<div style="color:var(--text-muted);font-size:var(--fs-label);">{_esc(item.evidence_id)} · '
            f"{_esc(item.source_name)} · {_esc(item.source_tier)} · {_esc(item.source_date)}</div>"
            f'<div style="color:var(--text);margin-top:var(--space-2);white-space:pre-wrap;">'
            f"{_esc(item.excerpt_or_locator)}</div>"
            + (f'<div style="color:var(--text-muted);font-size:var(--fs-label);margin-top:var(--space-2);">'
               f"sha256 {_esc(item.excerpt_sha256)}</div>" if item.excerpt_sha256 else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    section_header("Candidate context (read-only)")
    _render_candidate_context(packet)

    section_header("Audit trail")
    if not events:
        st.write("No audit events were recorded for this decision.")
    for event in events:
        st.markdown(
            f'<div style="display:flex;gap:var(--space-3);padding:var(--space-2) 0;'
            'border-bottom:1px solid var(--border-subtle);">'
            f'<span style="color:var(--text-muted);font-family:var(--font-mono);min-width:14rem;">'
            f"{_esc(event.created_at)}</span>"
            f'<span style="color:var(--text);min-width:12rem;">{_esc(event.event_type)}</span>'
            f'<span style="color:var(--text-secondary);flex:1;">{_esc(event.outcome)}</span></div>',
            unsafe_allow_html=True,
        )


def _render_candidate_context(packet) -> None:
    from src.data_access import backend_factory

    settings = get_settings()
    try:
        repo = backend_factory.get_candidate_repository(settings, packet.source)
        candidate = repo.get_candidate(packet.candidate_id)
    except Exception:  # noqa: BLE001
        candidate = None
    if candidate is None:
        st.write("The candidate row is not readable from this deployment.")
        return
    st.markdown(
        _chip("Status", getattr(candidate.status, "value", str(candidate.status)))
        + _chip("Radar confidence", str(candidate.confidence))
        + _chip("Extraction", getattr(candidate.extraction_state, "value", ""))
        + _chip("Reviewed at", candidate.reviewed_at or "never")
        + _chip("Published by", candidate.published_by or "nobody")
        + _chip("Filed", candidate.filing.rcept_dt or "—"),
        unsafe_allow_html=True,
    )
    if candidate.excerpt_original:
        st.markdown(
            '<div style="border:1px solid var(--border-subtle);border-radius:var(--r-sm);'
            'padding:var(--space-3);background:var(--surface-chip);white-space:pre-wrap;color:var(--text);">'
            f"{_esc(candidate.excerpt_original)}</div>",
            unsafe_allow_html=True,
        )


def _loads(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default

#!/usr/bin/env python3
"""p2_layer: translational evidence (clinical trials matching).

One P2 tool (probe-verified 2026-10-12):

  tcm_trials_match - ClinicalTrials.gov API v2 matching for candidate
                     targets/compounds: phase-stratified counts, top
                     recruiting/active trials, intervention lists -
                     the translational-evidence layer for candidate
                     prioritization.

OpenMM MD pilot: DEFERRED by decision (400MB install, cost/benefit
low for triage workflows on this machine) - not a dead end, recorded
as a deliberate deferral.

Disciplines: trial matches are TRANSLATIONAL CONTEXT (clinical
activity around a target/intervention), never efficacy proof of the
TCM candidate itself; live retrieval with source + queried_at.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_p2_layer")

CTGOV_V2 = "https://clinicaltrials.gov/api/v2/studies"

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%H:%M:%S UTC")


async def _ctgov(params: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.get(CTGOV_V2, params=params)
        r.raise_for_status()
        return r.json()


def _parse_studies(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for s in payload.get("studies", []):
        p = s.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        st = p.get("statusModule", {})
        des = p.get("designModule", {})
        arms = p.get("armsInterventionsModule", {})
        cond = p.get("conditionsModule", {})
        interventions = []
        for iv in (arms.get("interventions") or [])[:4]:
            interventions.append(
                f"{iv.get('type', '?')}: {iv.get('name', '?')}")
        rows.append({
            "nct_id": idm.get("nctId"),
            "title": (idm.get("briefTitle") or "")[:110],
            "status": st.get("overallStatus"),
            "phase": des.get("phases") or ["N/A"],
            "enrollment": des.get("enrollmentInfo", {}).get("count"),
            "start_date": (st.get("startDateStruct") or {}).get("date"),
            "conditions": (cond.get("conditions") or [])[:3],
            "interventions": interventions,
            "lead_sponsor": (p.get("sponsorCollaboratorsModule", {})
                             .get("leadSponsor", {}).get("name")),
            "why_stopped": st.get("whyStopped")})
    return rows


class TrialsMatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: str = Field(..., min_length=2, max_length=120,
                           description="disease condition to match "
                                       "(e.g. ulcerative colitis)")
    target_or_compound: Optional[str] = Field(
        None, max_length=80,
        description="gene/protein name (as intervention search, e.g. TNF) "
                    "OR compound name (e.g. berberine) to focus matching")
    focus: str = Field(
        "intervention", description="how to apply target_or_compound: "
                                    "intervention (query.intr) | term "
                                    "(query.term free-text)")
    phases: Optional[List[str]] = Field(
        None, max_length=4,
        description="filter to phases (e.g. [\"PHASE3\"]); omit for all")
    only_recruiting: bool = Field(
        False, description="limit to currently recruiting/active trials")
    page_size: int = Field(10, ge=1, le=30)
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_trials_match",
    annotations={"title": "ClinicalTrials.gov Translational Matching",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_trials_match(params: TrialsMatchInput) -> str:
    '''Match clinical trials for a condition + target/compound (CT.gov v2).

    Translational-evidence layer for candidate prioritization: queries the
    LIVE ClinicalTrials.gov API v2 and reports
      - total trial counts overall and phase-stratified (PHASE1..4)
      - top trials (NCT id, title, phase, status, enrollment, start date,
        interventions, lead sponsor)
      - optional recruiting/active-only filter

    INTERPRETATION DISCIPLINE: trial matches show CLINICAL ACTIVITY around
    a condition/target (e.g. 'TNF inhibitors are heavily trialled in UC')
    - translational context for target validation. They say NOTHING about
    efficacy of the TCM candidate compound itself. A target with rich
    trial activity is a lower-translational-risk target, not a validated
    indication for your compound.

    Args:
        params (TrialsMatchInput): condition + optional
            target_or_compound (+ focus mode) + phase filter.

    Returns:
        str: phase-stratified counts, trial table, discipline note.

    Examples:
        - "How much clinical trial activity targets TNF in UC?"
          -> condition="ulcerative colitis", target_or_compound="TNF"
        - "Any berberine trials for colitis?" -> condition="colitis",
          target_or_compound="berberine"
    '''
    base: Dict[str, Any] = {"query.cond": params.condition,
                            "pageSize": params.page_size,
                            "countTotal": "true",
                            "fields": "NCTId,BriefTitle,OverallStatus,"
                                      "Phase,EnrollmentCount,StartDate,"
                                      "Condition,InterventionName,"
                                      "LeadSponsorName,WhyStopped"}
    if params.target_or_compound:
        key = ("query.intr" if params.focus == "intervention"
               else "query.term")
        base[key] = params.target_or_compound
    if params.phases:
        base["filter.advanced"] = "AREA[Phase]" + " AND AREA[Phase]".join(
            f"({ph})" for ph in params.phases)
    if params.only_recruiting:
        base["filter.overallStatus"] = "RECRUITING"

    try:
        payload = await _ctgov(base)
        rows = _parse_studies(payload)
    except Exception as e:
        return f"Error: ClinicalTrials.gov query failed: {type(e).__name__}: {e}"

    # phase-stratified counts (parallel lightweight queries)
    phase_counts = {}
    if not params.phases:
        for ph in ("PHASE4", "PHASE3", "PHASE2", "PHASE1"):
            q = dict(base)
            q["filter.advanced"] = f"AREA[Phase]({ph})"
            q["pageSize"] = 1
            try:
                d = await _ctgov(q)
                phase_counts[ph.replace("PHASE", "Phase ")] = \
                    d.get("totalCount", 0)
                await asyncio.sleep(0.15)
            except Exception:
                phase_counts[ph.replace("PHASE", "Phase ")] = None

    data = {
        "condition": params.condition,
        "target_or_compound": params.target_or_compound,
        "focus": params.focus,
        "total_trials": payload.get("totalCount"),
        "phase_counts": phase_counts,
        "trials": rows,
        "interpretation": [
            "trial matches = translational context (clinical activity "
            "around the condition/target), NOT efficacy proof of the "
            "TCM candidate compound",
            "targets with rich trial activity carry lower translational "
            "risk (druggability precedent)",
            "combine with M2 cross-database evidence and literature "
            "counts (tcm_literature_mine) for the full evidence picture"],
        "source": "ClinicalTrials.gov API v2 (live)",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Clinical trials: {params.condition}"]
    if params.target_or_compound:
        lines.append(f"- focus: {params.target_or_compound} "
                     f"({params.focus})")
    lines.append(f"- total trials: {data['total_trials']}")
    if phase_counts:
        lines.append(f"- by phase: {json.dumps(phase_counts)}")
    lines.append("")
    lines.append("## Trials")
    lines.append("| NCT | phase | status | enrollment | title |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        lines.append(f"| {r['nct_id']} | {'/'.join(r['phase'])} | "
                     f"{r['status']} | {r.get('enrollment') or '-'} | "
                     f"{r['title'][:70]} |")
    for r in rows[:5]:
        if r.get("interventions"):
            lines.append(f"- {r['nct_id']} interventions: "
                         f"{'; '.join(r['interventions'][:3])}")
    lines.append("")
    for note in data["interpretation"]:
        lines.append(f"- {note}")
    lines.append(f"- source: {data['source']}; queried_at: {_now_iso()}")
    return "\n".join(lines)

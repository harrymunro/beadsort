"""Deriver for the built-in `triage` pack: who is this bead waiting on.

The typed successor to a COMMS / OWNER / CODE rubric. The model answers atomic questions;
the override rules below (code half wins when it can proceed, decisions demote to code
when they are technical, corroborate a hesitant category with an independent yes/no) are
the judgement calls that used to live in a prose rubric, now as code with visible
thresholds.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beadsort.packs import Answers, DeriveContext, Verdict

DIMENSIONS = ("waiting-on", "owner-kind", "ask-urgency", "stale")
URGENCY_LEVELS = ("whenever", "soon", "blocking")
STALE_QUESTIONS = {
    "superseded": "superseded",
    "no_consumer": "no-consumer",
    "umbrella_only": "umbrella",
}
PERSON_CATEGORIES = {"named_person", "owner_decision", "owner_admin"}


def _t(thresholds: Mapping[str, Any], key: str, default: Any) -> Any:
    return thresholds.get(key, default)


def _stale(answers: Answers, thresholds: Mapping[str, Any], verdict: Verdict) -> None:
    flag = float(_t(thresholds, "stale_flag", 0.75))
    best: tuple[str, float] | None = None
    for qid, value in STALE_QUESTIONS.items():
        p = answers.p(qid)
        if p is not None and p >= flag and (best is None or p > best[1]):
            best = (value, p)
    if best and verdict.labels.get("stale") is None:
        verdict.labels["stale"] = best[0]
        verdict.review.append(f"stale: {best[0]} (p={best[1]:.2f}); never auto-closed")


def _urgency(
    answers: Answers, thresholds: Mapping[str, Any], ctx: DeriveContext, verdict: Verdict
) -> None:
    from_priority = _t(thresholds, "urgency_from_priority", {0: "blocking"}) or {}
    if ctx.bead.priority is not None:
        forced = from_priority.get(ctx.bead.priority, from_priority.get(str(ctx.bead.priority)))
        if forced:
            verdict.labels["ask-urgency"] = str(forced)
            verdict.meta["urgency_from_priority"] = True
            return
    score = answers.score("ask_urgency")
    if score is None:
        return
    if answers.conf("ask_urgency") < float(_t(thresholds, "urgency_conf", 0.5)):
        verdict.uncertain.append("ask-urgency")
        return
    verdict.labels["ask-urgency"] = URGENCY_LEVELS[
        min(len(URGENCY_LEVELS) - 1, max(0, round(score)))
    ]


def derive(answers: Answers, thresholds: Mapping[str, Any], ctx: DeriveContext) -> Verdict:
    verdict = Verdict(labels=dict.fromkeys(DIMENSIONS))
    noul_yes = float(_t(thresholds, "noul_yes", 0.70))
    choice_act = float(_t(thresholds, "choice_act", 0.60))
    choice_caution = float(_t(thresholds, "choice_caution", 0.35))
    person_act = float(_t(thresholds, "person_act", 0.70))
    band = _t(thresholds, "done_review_band", [0.40, 0.70])
    code_override = _t(thresholds, "code_override", {}) or {}
    hc_min = float(code_override.get("has_code_half", 0.70))
    cs_min = float(code_override.get("code_can_start_now", 0.75))

    done_p = answers.p("already_done") or 0.0
    category = answers.choice("blocking_party")
    cat_conf = answers.conf("blocking_party")
    verdict.meta["category"] = category
    verdict.meta["category_confidence"] = round(cat_conf, 3)

    # 1. A satisfied ask wins over everything: the latest update says it already happened.
    if done_p >= noul_yes or (category == "already_done" and cat_conf >= choice_act):
        verdict.labels["stale"] = "done"
        verdict.review.append(f"ask satisfied per latest update (p={done_p:.2f})")
        return verdict

    # 2. Genuine uncertainty about the category.
    if category is None or cat_conf < choice_caution:
        verdict.uncertain.append("waiting-on")
        verdict.review.append(f"category unclear (top: {answers.describe_top('blocking_party')})")
        _stale(answers, thresholds, verdict)
        return verdict

    has_code = answers.p("has_code_half") or 0.0
    can_start = answers.p("code_can_start_now") or 0.0
    stakeholder = answers.choice("stakeholder")

    # 3. Bucket of the blocking half: a code half that can proceed makes the bead agent work.
    if category in PERSON_CATEGORIES and has_code >= hc_min and can_start >= cs_min:
        verdict.meta["override"] = "code-half-can-start"
        if stakeholder in ctx.config.people:
            verdict.meta["also_ask"] = stakeholder
        category = "coding_agent"

    # 4. "Decide X" is the owner's only if it needs their judgement AND blocks the work.
    if category == "owner_decision" and has_code >= 0.60:
        needs_owner = answers.p("decision_needs_owner")
        blocking = answers.p("decision_is_blocking")
        if (needs_owner is not None and needs_owner <= 0.35) or (
            blocking is not None and blocking <= 0.30
        ):
            verdict.meta["override"] = "technical-or-non-blocking-decision"
            category = "coding_agent"

    # 5. A hesitant category needs corroboration from the independent yes/no question.
    waiting_p = answers.p("waiting_on_person") or 0.0
    if choice_caution <= cat_conf < choice_act:
        if category == "named_person":
            corroborated = waiting_p >= noul_yes
        elif category in {"owner_decision", "owner_admin"}:
            corroborated = waiting_p <= 0.30
        elif category == "coding_agent":
            corroborated = waiting_p <= 0.30 and has_code >= hc_min
        elif category == "already_done":
            corroborated = done_p >= 0.50
        else:
            corroborated = True
        if not corroborated:
            verdict.uncertain.append("waiting-on")
            verdict.review.append(
                f"category {category} at {cat_conf:.2f} not corroborated "
                f"(waiting_on_person={waiting_p:.2f})"
            )
            _stale(answers, thresholds, verdict)
            return verdict

    # 6. Map the category to labels.
    if category == "named_person":
        st_conf = answers.conf("stakeholder")
        if stakeholder in ctx.config.people and st_conf >= person_act and waiting_p >= 0.50:
            verdict.labels["waiting-on"] = stakeholder
        elif stakeholder == "unspecified" and st_conf >= 0.60:
            verdict.labels["waiting-on"] = "owner"
            verdict.labels["owner-kind"] = "decision"
            verdict.meta["reason"] = "whom to ask"
        else:
            verdict.labels["waiting-on"] = "person"
            verdict.review.append(
                f"person unclear or not in config (top: {answers.describe_top('stakeholder')})"
            )
        _urgency(answers, thresholds, ctx, verdict)
    elif category == "owner_decision":
        verdict.labels["waiting-on"] = "owner"
        verdict.labels["owner-kind"] = "decision"
    elif category == "owner_admin":
        verdict.labels["waiting-on"] = "owner"
        verdict.labels["owner-kind"] = "admin"
        kind = answers.choice("owner_action_kind")
        if kind:
            verdict.meta["owner_action"] = kind
            if kind == "decision" and answers.conf("owner_action_kind") >= 0.60:
                verdict.review.append("admin category but action kind says decision")
    elif category == "coding_agent":
        verdict.labels["waiting-on"] = "agent"
    elif category == "parked":
        verdict.labels["waiting-on"] = "nobody"
    elif category == "already_done":
        verdict.labels["stale"] = "done"
        verdict.review.append("ask satisfied per latest update (hesitant, corroborated)")
    else:  # unclear
        verdict.uncertain.append("waiting-on")
        verdict.review.append("next step unclear from the bead text")

    # Sanity: an admin/decision mismatch is worth a look.
    if category == "owner_decision":
        kind = answers.choice("owner_action_kind")
        if (
            kind
            and kind not in {"decision", "not_applicable"}
            and answers.conf("owner_action_kind") >= 0.60
        ):
            verdict.review.append(f"decision category but action kind says {kind}")

    # 7. Possibly satisfied: keep labels, ask a human.
    if band and float(band[0]) <= done_p < float(band[1]):
        verdict.review.append(f"possibly satisfied per latest update (p={done_p:.2f})")

    # 8. Stale flags. Never auto-close.
    _stale(answers, thresholds, verdict)

    # 9. Epics: note when every open child is already off-board.
    if ctx.bead.is_epic and ctx.children:
        open_children = [c for c in ctx.children if c.status not in ctx.config.done_statuses]
        if open_children and all(
            any(
                label.startswith("waiting-on:") and not label.endswith(":agent")
                for label in c.labels
            )
            for c in open_children
        ):
            verdict.meta["children_all_offboard"] = True
    return verdict

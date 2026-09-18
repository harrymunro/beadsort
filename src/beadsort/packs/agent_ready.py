"""Deriver for the built-in `agent-ready` pack: can an agent start this bead cold?

An open blocking dependency decides the answer before the model is called (`precheck`).
Otherwise the hard-no reasons are checked in precedence order and the first one wins as
the `blocker` label. A clean yes needs every signal on the safe side of its threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beadsort.packs import Answers, DeriveContext, Verdict

DIMENSIONS = ("agent-ready", "blocker")
RESOURCE_BLOCKERS = {
    "private_file_or_data": "private-file",
    "secret_or_network": "secret-or-network",
    "deployed_environment": "deployed-env",
}


def precheck(thresholds: Mapping[str, Any], ctx: DeriveContext) -> Verdict | None:
    """An open blocking dependency settles the answer without a model call."""
    if not ctx.open_blockers:
        return None
    ids = [b.id for b in ctx.open_blockers]
    return Verdict(
        labels={"agent-ready": "no", "blocker": "dependency"},
        meta={
            "open_blockers": ids,
            "no_model_call": True,
            "note": f"waits on {', '.join(ids[:3])}",
        },
    )


def derive(answers: Answers, thresholds: Mapping[str, Any], ctx: DeriveContext) -> Verdict:
    verdict = Verdict(labels=dict.fromkeys(DIMENSIONS))
    yes = float(thresholds.get("yes", 0.30))  # noul at or below this: no obstacle
    no = float(thresholds.get("no", 0.70))  # noul at or above this: confident obstacle
    choice_act = float(thresholds.get("choice_act", 0.60))
    spec_ready = float(thresholds.get("spec_ready", 1.5))
    spec_conf = float(thresholds.get("spec_conf", 0.50))
    spec_block = float(thresholds.get("spec_block", 1.0))

    human = answers.p("needs_human_input_first") or 0.0
    owner = answers.p("requires_owner_judgement") or 0.0
    done = answers.p("already_done") or 0.0
    is_code = answers.p("is_code_work")
    is_code = 1.0 if is_code is None else is_code
    resource = answers.choice("needs_external_resource")
    resource_conf = answers.conf("needs_external_resource")
    contained = answers.p("scope_contained")
    contained = 1.0 if contained is None else contained
    target = answers.choice("target_repo")
    target_conf = answers.conf("target_repo")
    spec_score = answers.score("spec_clarity")
    spec_c = answers.conf("spec_clarity")
    elsewhere = answers.p("spec_lives_elsewhere") or 0.0

    verdict.meta.update(
        {
            "needs_human_input_first": round(human, 3),
            "requires_owner_judgement": round(owner, 3),
            "already_done": round(done, 3),
            "is_code_work": round(is_code, 3),
            "resource": resource,
            "spec_clarity": None if spec_score is None else round(spec_score, 2),
        }
    )

    blocker: str | None = None
    if human >= no:
        blocker = "human-input"
    elif owner >= no:
        blocker = "owner-decision"
    elif done >= no:
        blocker = "done"
    elif is_code <= yes:
        blocker = "not-code"
    elif resource in RESOURCE_BLOCKERS and resource_conf >= choice_act:
        blocker = RESOURCE_BLOCKERS[resource]
    elif (
        contained <= yes
        and target not in {None, "this_repo", "unclear", "not_repo_work"}
        and target_conf >= choice_act
    ):
        blocker = "off-board"
        verdict.meta["target_repo"] = target
    elif spec_score is not None and spec_score <= spec_block and spec_c >= spec_conf:
        blocker = (
            "private-file" if elsewhere >= no and resource == "private_file_or_data" else "spec"
        )

    if blocker:
        verdict.labels["agent-ready"] = "no"
        verdict.labels["blocker"] = blocker
        return verdict

    clean = (
        human <= yes
        and owner <= yes
        and done <= yes
        and is_code >= no
        and resource in {"none", "person_check"}
        and resource_conf >= choice_act
        and spec_score is not None
        and spec_score >= spec_ready
        and spec_c >= spec_conf
        and contained >= 0.50
    )
    if clean:
        verdict.labels["agent-ready"] = "yes"
        verdict.meta["needs_person_check"] = resource == "person_check"
        if elsewhere >= no:
            verdict.meta["spec_source"] = "external"
        return verdict

    verdict.uncertain.append("agent-ready")
    reasons = []
    if yes < human < no:
        reasons.append(f"human input p={human:.2f}")
    if yes < owner < no:
        reasons.append(f"owner judgement p={owner:.2f}")
    if yes < done < no:
        reasons.append(f"already done p={done:.2f}")
    if yes < is_code < no:
        reasons.append(f"code work p={is_code:.2f}")
    if resource not in {"none", "person_check"} or resource_conf < choice_act:
        reasons.append(f"resource {resource} conf={resource_conf:.2f}")
    if spec_score is None or spec_score < spec_ready or spec_c < spec_conf:
        reasons.append(f"spec clarity {spec_score} conf={spec_c:.2f}")
    if contained < 0.50:
        reasons.append(f"scope contained p={contained:.2f}")
    verdict.review.append("agent-ready unsure: " + "; ".join(reasons))
    return verdict

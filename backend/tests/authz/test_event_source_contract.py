"""Source crosswalk completeness is a schema draft gate, not runtime coverage."""

import re
from pathlib import Path

from schema_drafts.org_authz.event_contract import DELETE_ORDER, WRITE_ORDER, event_contracts
from schema_drafts.org_authz.plan_source import source_rows

PLAN = Path(__file__).parents[3] / "docs/exec-plans/org-authz-plan.html"


def test_every_original_event_cell_has_one_exact_contract() -> None:
    # Given: independently parsed original source, preserving all five semantic cells.
    original = source_rows(PLAN, "7.2.1")
    # When: the checked-in crosswalk is parsed through its typed boundary.
    contracts = event_contracts()
    # Then: duplicate, missing, reordered, renamed or changed source events fail.
    assert len(original) == 19
    assert len({event.source_cells[0] for event in contracts}) == 19
    assert tuple(event.source_cells for event in contracts) == original


def test_projection_ordering_is_explicit_for_all_source_events() -> None:
    # Given: every original event's resolved tuple additions/deletions and marker scope.
    contracts = event_contracts()
    # When: transaction and revocation orders are compared to the 8.6 contract.
    errors: list[str] = []
    for event in contracts:
        if event.writes and event.write_order != WRITE_ORDER:
            errors.append(event.source_cells[0] + ": writes")
        if event.deletes and event.scopes and event.delete_order != DELETE_ORDER:
            errors.append(event.source_cells[0] + ": revocation")
        if event.deletes and not event.scopes and event.resolution != "R7":
            errors.append(event.source_cells[0] + ": missing marker")
    # Then: only the documented gated tenant purge is exempt from per-resource markers.
    assert errors == []


def test_each_original_tuple_effect_and_marker_is_mapped() -> None:
    # Given: original source cells carry additions, removals and seven marker variants.
    contracts = event_contracts()
    scopes = r"grant|membership|group_member|role|capability|ownership|resource"
    # When: the original tuple-presence and marker semantics are independently extracted.
    errors: list[str] = []
    for event in contracts:
        label, _, additions, removals, markers = event.source_cells
        expected_scopes = tuple(re.findall(scopes, markers))
        if tuple(event.scopes) != expected_scopes:
            errors.append(label + ": marker scope")
        if bool(event.writes) != (additions != "-"):
            errors.append(label + ": write effect")
        if bool(event.deletes) != (removals != "-" and not removals.startswith("-(")):
            errors.append(label + ": delete effect")
    # Then: every advertised effect has sources, including variant add/remove rows.
    assert errors == []

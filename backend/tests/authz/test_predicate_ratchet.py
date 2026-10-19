"""The ownership ratchet detects syntax variants and equal-count replacements."""

from tests.project_gate_wave_support import load_module


def test_multiline_and_reversed_predicates_are_detected_when_scanned():
    # Given: equivalent owner filters expressed in different syntax.
    scanner = load_module("check_authz_predicates")
    source = """
query.where(Agent.user_id == caller.id)
query.where(caller.id == Skill.user_id)
query.where(
    MarketplaceItem.owner_user_id
    == caller.id
)
Tool.visible_to(caller.id)
def visible_to(user): pass
# Agent.user_id == ignored.id
message = "Agent.user_id == ignored.id"
"""
    # When: the syntax tree is scanned.
    found = scanner.scan_source(source)
    # Then: only executable predicates and the visibility seam are counted.
    assert len(found) == 5


def test_new_predicate_is_rejected_when_a_different_old_one_is_removed():
    # Given: a reviewed old predicate and a different new predicate.
    scanner = load_module("check_authz_predicates")
    old = scanner.PredicateRecord(
        file="service.py", expression="Agent.user_id == caller.id", count=1
    )
    new = scanner.PredicateRecord(
        file="service.py", expression="Skill.user_id == caller.id", count=1
    )
    # When: inventories with equal total counts are compared.
    added = scanner.additions((new,), (old,))
    # Then: replacement cannot evade the ratchet.
    assert added == (new,)


def test_duplicate_predicate_is_rejected_when_added_at_a_second_site():
    # Given: the same predicate already exists once in the file.
    scanner = load_module("check_authz_predicates")
    old = scanner.PredicateRecord(
        file="service.py", expression="Agent.user_id == caller.id", count=1
    )
    repeated = old.model_copy(update={"count": 2})
    # When: a second copy is introduced.
    added = scanner.additions((repeated,), (old,))
    # Then: the extra copy is identified.
    assert len(added) == 1
    assert added[0].count == 1

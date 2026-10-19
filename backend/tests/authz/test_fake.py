"""The fake grants only the explicit subject/action/resource combination."""

from app.authz import fake
from app.authz.client import CheckQuery, ListQuery


async def test_fake_denies_other_subjects_when_one_subject_is_authorized():
    # Given: one explicitly permitted tuple.
    backend = fake.FakeAuthz(frozenset({("user:a", "can_run", "agent:one")}))
    # When: both users attempt the same action.
    results = await backend.batch_check(
        (
            CheckQuery(user="user:a", relation="can_run", object="agent:one"),
            CheckQuery(user="user:b", relation="can_run", object="agent:one"),
        )
    )
    # Then: authorization is not inferred from object existence.
    assert results == (True, False)


async def test_fake_lists_only_matching_type_and_action_when_filtered():
    # Given: distinct actions, subjects and resource types.
    backend = fake.FakeAuthz(
        frozenset(
            {
                ("user:a", "can_use", "skill:one"),
                ("user:b", "can_use", "skill:two"),
                ("user:a", "can_edit", "skill:three"),
                ("user:a", "can_use", "tool:four"),
            }
        )
    )
    # When: a skill list is requested.
    result = await backend.list_objects(ListQuery(user="user:a", relation="can_use", type="skill"))
    # Then: unrelated grants do not leak into the result.
    assert result == ("skill:one",)

"""Rule tests use explicit events and no language model or network."""

from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from story2script.logic_engine import World, fact, reason_story
from story2script.logic_models import (
    Entity,
    Event,
    Evidence,
    InitialFact,
    SceneLogic,
    StoryLogic,
    ElementCoverage,
)
from story2script.plot_state import PlotChange


ENTITIES = {
    "a": "character",
    "b": "character",
    "c": "character",
    "key": "item",
    "letter": "item",
    "room": "location",
    "street": "location",
    "secret": "secret",
}


def opening():
    values = [
        fact(c, p, v)
        for c in ("a", "b", "c")
        for p, v in [("alive", "true"), ("available", "true"), ("at", "room")]
    ]
    return values + [
        fact("key", "owner", "a"),
        fact("letter", "owner", "a"),
        fact("letter", "contains", "true", "secret"),
    ]


def world(values=None):
    return World(
        ENTITIES,
        {
            f.key(): (f, f"seed:{i}", "")
            for i, f in enumerate(opening() if values is None else values)
        },
    )


def event(kind, actor="a", **kwargs):
    return Event(id=f"{actor}-{kind}", kind=kind, actor=actor, **kwargs)


def replay(groups, values=None, changes=(), **kwargs):
    scenes, ledgers = [], []
    for i, group in enumerate(groups):
        sid = f"s{i}"
        elements = [NS(type="action", text=f"{sid} {e.id}") for e in group] or [
            NS(type="action", text="风声")
        ]
        scenes.append(
            NS(
                id=sid,
                elements=elements,
                characters_present=[],
                timeline_mode="present",
                event_time_scene_id="",
            )
        )
        events = []
        for j, e in enumerate(group):
            e = e.model_copy(deep=True)
            e.evidence = [Evidence(scene_id=sid, element_index=j, quote=elements[j].text)]
            events.append(e)
        ledgers.append(
            SceneLogic(
                scene_id=sid,
                events=events,
                coverage=[
                    ElementCoverage(
                        element_index=j,
                        event_ids=[events[j].id] if events else [],
                        context_only=not events,
                        explanation="测试剧情",
                    )
                    for j in range(len(elements))
                ],
            )
        )
    script = NS(scenes=scenes, characters=[NS(id=c) for c in ("a", "b", "c")])
    story = StoryLogic(
        entities=[Entity(id=k, name=k, kind=v) for k, v in ENTITIES.items()],
        initial_facts=[
            InitialFact(fact=f, assumption=True, explanation="测试初始状态")
            for f in (opening() if values is None else values)
        ],
        scenes=ledgers,
    )
    return script, story, reason_story(script, story, changes, **kwargs)


def change(kind, sid="s0", actor="a"):
    return PlotChange(
        change_id=f"change-{kind}",
        operation=kind,
        character_id=actor,
        effective_scene_id=sid,
        instruction=kind,
    )


@pytest.mark.parametrize(
    "kind,extra,expected",
    [
        ("death", {}, fact("a", "alive", "false")),
        ("exit", {}, fact("a", "available", "false")),
        ("transfer", {"target": "b", "item": "key"}, fact("key", "owner", "b")),
        ("move", {"destination": "street"}, fact("a", "at", "street")),
        (
            "learn",
            {"secret": "secret", "source_item": "letter"},
            fact("a", "knows", "true", "secret"),
        ),
        (
            "relationship",
            {"target": "b", "relationship": "enemy"},
            fact("a", "relationship", "enemy", "b"),
        ),
    ],
)
def test_typed_effects(kind, extra, expected):
    state = world()
    step, error = state.apply(event(kind, **extra), "s0")
    assert error is None and step.status == "applied"
    assert state.require(expected).status == "true"
    if kind == "relationship":
        assert state.require(fact("b", "relationship", "enemy", "a")).status == "true"


@pytest.mark.parametrize("outgoing,incoming", [("death", "revive"), ("exit", "return")])
def test_explicit_recovery_unlocks_lifecycle(outgoing, incoming):
    state = world()
    assert state.apply(event(outgoing), "s0", approved=True)[1] is None
    assert state.apply(event("act"), "s1")[0].status == "blocked"
    assert state.apply(event(incoming), "s1")[0].status == "blocked"
    assert state.apply(event(incoming), "s1", approved=True)[1] is None
    assert state.apply(event("act"), "s2")[1] is None


def test_return_does_not_revive_and_revive_requires_death():
    assert world().apply(event("revive"), "s0")[0].status == "blocked"
    state = world()
    state.apply(event("death"), "s0")
    assert state.apply(event("return"), "s1", approved=True)[0].status == "blocked"


def test_unknown_is_distinct_from_false_and_never_a_premise():
    state = world([])
    assert state.require(fact("a", "alive", "false")).status == "unknown"
    step, error = state.apply(event("act"), "s0")
    assert error and all(c.status == "unknown" for c in step.checks)
    assert state.values == {}


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("transfer", {"target": "b", "item": "key"}),
        ("learn", {"secret": "secret", "source_character": "b"}),
    ],
)
def test_location_must_be_known_and_shared(kind, extra):
    state = world()
    state.apply(event("move", actor="b", destination="street"), "s0")
    before = dict(state.values)
    step, error = state.apply(event(kind, **extra), "s1")
    assert error and step.status == "blocked" and state.values == before
    state.values.pop(("a", "at", ""))
    step, _ = state.apply(event(kind, **extra), "s2")
    assert any(c.status == "unknown" and c.expected.predicate == "at" for c in step.checks)


def test_single_owner_and_atomic_failure():
    state = world()
    state.apply(event("transfer", target="b", item="key"), "s0")
    before = dict(state.values)
    step, error = state.apply(event("transfer", target="c", item="key"), "s1")
    assert error and step.effects == [] and state.values == before
    assert state.require(fact("key", "owner", "b")).status == "true"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"source_character": "a"},
        {"source_character": "b"},
        {"source_item": "key"},
        {"source_character": "b", "source_item": "letter"},
    ],
)
def test_information_needs_real_source(extra):
    step, error = world().apply(event("learn", secret="secret", **extra), "s0")
    assert error and step.status == "blocked" and not step.effects


def test_information_from_owned_text_then_person_has_provenance():
    state = world()
    learn_a = event("learn", secret="secret", source_item="letter")
    assert state.apply(learn_a, "s0")[1] is None
    step, error = state.apply(
        event("learn", actor="b", secret="secret", source_character="a"), "s1"
    )
    assert error is None and learn_a.id in step.causes


@pytest.mark.parametrize(
    "kind,extra",
    [
        ("move", {"destination": "key"}),
        ("death", {"target": "b"}),
        ("transfer", {"target": "b"}),
        ("relationship", {"target": "a", "relationship": "ally"}),
    ],
)
def test_bad_slots_and_entity_types_rejected(kind, extra):
    state = world()
    before = dict(state.values)
    step, error = state.apply(event(kind, **extra), "s0")
    assert error and not step.effects and state.values == before


def test_models_never_accept_custom_executable_rules():
    with pytest.raises(ValidationError):
        Event(id="x", kind="python", actor="a", code="arbitrary")


def chain():
    return [
        [event("act")],
        [event("transfer", item="letter", target="b")],
        [event("learn", actor="b", secret="secret", source_item="letter")],
        [event("learn", actor="c", secret="secret", source_character="b")],
        [event("act", actor="c", requires=[fact("c", "knows", "true", "secret")])],
    ]


def test_causal_chain_and_counterfactual_invalidation():
    script, story, baseline = replay(chain())
    assert baseline.valid
    assert baseline.dependencies == {"s2": ["s1"], "s3": ["s2"], "s4": ["s3"]}
    after = reason_story(script, story, intervention=change("death"))
    assert not after.valid
    blocked = {s.scene_id for s in after.steps if s.status == "blocked"}
    assert blocked == {"s1", "s2", "s3", "s4"}
    assert (
        next(s for s in after.steps if s.event_id.startswith("intervention:")).status == "applied"
    )


def test_initial_conflicts_and_symmetric_relations():
    _, _, report = replay([[]], opening() + [fact("key", "owner", "b")])
    assert not report.valid and report.issues[0].code == "initial_fact"
    _, _, report = replay([[]], opening() + [fact("a", "relationship", "ally", "b")])
    assert report.valid and fact("b", "relationship", "ally", "a") in report.final_facts
    _, _, report = replay(
        [[]],
        opening()
        + [fact("a", "relationship", "ally", "b"), fact("b", "relationship", "enemy", "a")],
    )
    assert not report.valid


@pytest.mark.parametrize(
    "mutation", ["quote", "coverage", "unknown_scene", "duplicate_event", "reserved_id", "order"]
)
def test_grounding_and_structural_gates(mutation):
    script, story, _ = replay([[event("act"), event("move", destination="street")]])
    if mutation == "quote":
        story.scenes[0].events[0].evidence[0].quote = "fabricated"
    elif mutation == "coverage":
        story.scenes[0].coverage.pop()
    elif mutation == "unknown_scene":
        story.scenes[0].scene_id = "missing"
    elif mutation == "duplicate_event":
        story.scenes[0].events[1].id = story.scenes[0].events[0].id
    elif mutation == "reserved_id":
        story.scenes[0].events[0].id = "intervention:fake"
    else:
        story.scenes[0].events.reverse()
    report = reason_story(script, story)
    assert not report.valid and not report.steps


def test_missing_initial_facts_and_bad_citations_not_silently_seeded():
    script, story, _ = replay([[event("act")]])
    story.initial_facts[0].assumption = False
    assert not reason_story(script, story).valid
    story.initial_facts[0].evidence = [
        Evidence(scene_id="s0", element_index=0, quote="not present")
    ]
    assert not reason_story(script, story).valid


def test_speech_and_presence_cannot_hide_in_context_only():
    script, story, _ = replay([[event("death")], []])
    script.scenes[1].elements[0] = NS(type="dialogue", text="我还在", character="a")
    report = reason_story(script, story, [change("death")])
    assert any(s.event_id == "speech:s1:0" and s.status == "blocked" for s in report.steps)
    script.scenes[1].elements[0].type = "action"
    script.scenes[1].characters_present = ["a"]
    assert any(
        s.event_id == "presence:s1:a" and s.status == "blocked"
        for s in reason_story(script, story, [change("death")]).steps
    )


def test_goal_must_be_grounded_unique_and_last():
    script, story, report = replay([[event("death")]], changes=[change("death")])
    assert report.valid
    _, _, report = replay([[event("death"), event("act", actor="b")]], changes=[change("death")])
    assert any(i.code == "goal" for i in report.issues)
    story.scenes[0].events.clear()
    story.scenes[0].coverage[0].event_ids = []
    story.scenes[0].coverage[0].context_only = True
    assert not reason_story(script, story, [change("death")]).valid


def test_explicit_revive_after_death_and_action_is_valid():
    _, _, report = replay(
        [[event("death")], [event("revive")], [event("act")]],
        changes=[change("death"), change("revive", "s1")],
    )
    assert report.valid


def test_flashback_uses_start_snapshot_and_cannot_change_present():
    script, story, _ = replay([[event("death")], [event("transfer", item="key", target="b")]])
    script.scenes[1].timeline_mode = "flashback"
    script.scenes[1].event_time_scene_id = "s0"
    report = reason_story(script, story, [change("death")])
    assert report.valid
    assert fact("key", "owner", "a") in report.final_facts
    assert fact("a", "alive", "false") in report.final_facts
    script.scenes[1].event_time_scene_id = "s1"
    assert not reason_story(script, story).valid


def test_invalid_cutoff_returns_issue_and_future_state_does_not_leak():
    script, story, _ = replay(chain())
    assert not reason_story(script, story, stop_after="missing").valid
    report = reason_story(script, story, stop_before="s2")
    assert report.valid
    assert fact("b", "knows", "true", "secret") not in report.final_facts
    assert report.scanned_scene_ids == ["s0", "s1"]


def test_dialogue_after_end_change_is_not_hidden_by_context_marker():
    script, story, _ = replay([[event("death")]])
    script.scenes[0].elements.append(NS(type="dialogue", text="后来", character="b"))
    story.scenes[0].coverage.append(
        ElementCoverage(element_index=1, context_only=True, explanation="对白")
    )
    assert any(i.code == "goal" for i in reason_story(script, story, [change("death")]).issues)

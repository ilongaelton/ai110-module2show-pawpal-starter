"""Tests for PawPal+ scheduling behaviors.

These cover the behaviors the README's "Smarter Scheduling" table promises:
sorting by priority, filtering when time runs out, conflict handling for
fixed appointments, and recurring (daily vs. weekly) tasks.
"""

import pytest

from pawpal_system import (
    DailyPlan,
    Owner,
    Pet,
    Priority,
    Recurrence,
    ScheduledTask,
    Scheduler,
    Task,
    build_plan,
)


@pytest.fixture
def owner():
    return Owner(name="Jordan", available_minutes=120, day_start="08:00")


@pytest.fixture
def pet():
    return Pet(name="Mochi", species="dog", breed="Shiba Inu")


# --- Task validation -------------------------------------------------------


def test_priority_from_str_is_case_insensitive():
    assert Priority.from_str("high") is Priority.HIGH
    assert Priority.from_str("HIGH") is Priority.HIGH
    assert Priority.from_str(Priority.LOW) is Priority.LOW


def test_priority_ordering():
    assert Priority.HIGH > Priority.MEDIUM > Priority.LOW


def test_invalid_priority_raises():
    with pytest.raises(ValueError):
        Task("Walk", 20, priority="urgent")


def test_non_positive_duration_raises():
    with pytest.raises(ValueError):
        Task("Walk", 0)
    with pytest.raises(ValueError):
        Task("Walk", -5)


def test_weekly_task_requires_weekday():
    with pytest.raises(ValueError):
        Task("Bath", 30, recurrence="weekly")


# --- Sorting by priority ---------------------------------------------------


def test_higher_priority_scheduled_before_lower(owner, pet):
    tasks = [
        Task("Low task", 20, priority="low"),
        Task("High task", 20, priority="high"),
        Task("Medium task", 20, priority="medium"),
    ]
    plan = build_plan(owner, pet, tasks)
    titles = [s.task.title for s in plan.scheduled]
    assert titles == ["High task", "Medium task", "Low task"]


def test_tie_break_prefers_shorter_task(owner, pet):
    tasks = [
        Task("Long high", 40, priority="high"),
        Task("Short high", 10, priority="high"),
    ]
    plan = build_plan(owner, pet, tasks)
    assert plan.scheduled[0].task.title == "Short high"


def test_prefer_short_first_false_schedules_longer_first(pet):
    owner = Owner(name="Sam", available_minutes=120, prefer_short_first=False)
    tasks = [
        Task("Short high", 10, priority="high"),
        Task("Long high", 40, priority="high"),
    ]
    plan = build_plan(owner, pet, tasks)
    assert plan.scheduled[0].task.title == "Long high"


# --- Filtering when time runs out ------------------------------------------


def test_low_priority_task_skipped_when_out_of_time(pet):
    owner = Owner(name="Jordan", available_minutes=30)
    tasks = [
        Task("Walk", 30, priority="high"),
        Task("Enrichment", 20, priority="low"),
    ]
    plan = build_plan(owner, pet, tasks)
    scheduled_titles = [s.task.title for s in plan.scheduled]
    skipped_titles = [s.task.title for s in plan.skipped]
    assert scheduled_titles == ["Walk"]
    assert skipped_titles == ["Enrichment"]
    assert "ran out of time" in plan.skipped[0].reason


def test_total_scheduled_never_exceeds_budget(pet):
    owner = Owner(name="Jordan", available_minutes=45)
    tasks = [Task(f"Task {i}", 20, priority="medium") for i in range(5)]
    plan = build_plan(owner, pet, tasks)
    assert plan.total_scheduled_minutes <= owner.available_minutes
    assert plan.remaining_minutes >= 0


def test_zero_budget_skips_everything(pet):
    owner = Owner(name="Jordan", available_minutes=0)
    plan = build_plan(owner, pet, [Task("Walk", 10)])
    assert plan.scheduled == []
    assert len(plan.skipped) == 1


def test_empty_task_list_returns_empty_plan(owner, pet):
    plan = build_plan(owner, pet, [])
    assert plan.scheduled == []
    assert plan.skipped == []
    assert "no tasks scheduled" in plan.render()


# --- Conflict handling (fixed appointments) --------------------------------


def test_fixed_task_placed_at_its_time(owner, pet):
    tasks = [Task("Vet", 30, priority="high", fixed_start="10:00")]
    plan = build_plan(owner, pet, tasks)
    assert plan.scheduled[0].start == "10:00"
    assert plan.scheduled[0].end == "10:30"


def test_overlapping_appointments_keep_higher_priority(owner, pet):
    tasks = [
        Task("Vet visit", 60, priority="high", fixed_start="09:00"),
        Task("Groomer", 60, priority="low", fixed_start="09:30"),
    ]
    plan = build_plan(owner, pet, tasks)
    scheduled_titles = [s.task.title for s in plan.scheduled]
    assert "Vet visit" in scheduled_titles
    assert "Groomer" not in scheduled_titles
    assert "conflicts with" in plan.skipped[0].reason


def test_flexible_task_routed_around_appointment(pet):
    owner = Owner(name="Jordan", available_minutes=300, day_start="08:00")
    tasks = [
        Task("Vet", 60, priority="high", fixed_start="08:30"),
        Task("Walk", 30, priority="high"),
    ]
    plan = build_plan(owner, pet, tasks)
    walk = next(s for s in plan.scheduled if s.task.title == "Walk")
    vet = next(s for s in plan.scheduled if s.task.title == "Vet")
    # The flexible walk must not overlap the fixed vet appointment.
    assert not walk.overlaps(vet)


def test_no_two_scheduled_tasks_overlap(pet):
    owner = Owner(name="Jordan", available_minutes=300)
    tasks = [
        Task("Vet", 45, priority="high", fixed_start="09:00"),
        Task("Walk", 30, priority="high"),
        Task("Feed", 15, priority="high"),
        Task("Play", 20, priority="medium"),
    ]
    plan = build_plan(owner, pet, tasks)
    for a, b in zip(plan.scheduled, plan.scheduled[1:]):
        assert not a.overlaps(b)


# --- Recurring tasks -------------------------------------------------------


def test_weekly_task_skipped_on_wrong_day(owner, pet):
    # Bath runs Sundays (weekday 6); plan for a Monday (0).
    tasks = [
        Task("Feed", 10, priority="high"),
        Task("Bath", 30, priority="medium", recurrence="weekly", weekday=6),
    ]
    plan = build_plan(owner, pet, tasks, weekday=0)
    scheduled = [s.task.title for s in plan.scheduled]
    assert "Bath" not in scheduled
    assert "Feed" in scheduled


def test_weekly_task_included_on_right_day(owner, pet):
    tasks = [Task("Bath", 30, recurrence="weekly", weekday=2)]
    plan = build_plan(owner, pet, tasks, weekday=2)
    assert [s.task.title for s in plan.scheduled] == ["Bath"]


def test_daily_task_always_included(owner, pet):
    task = Task("Feed", 10, recurrence="daily")
    for weekday in range(7):
        plan = build_plan(owner, pet, [task], weekday=weekday)
        assert len(plan.scheduled) == 1


def test_no_weekday_means_no_recurrence_filter(owner, pet):
    # When weekday is None the planner ignores recurrence entirely.
    tasks = [Task("Bath", 30, recurrence="weekly", weekday=6)]
    plan = build_plan(owner, pet, tasks, weekday=None)
    assert [s.task.title for s in plan.scheduled] == ["Bath"]


# --- Rendering / explanation -----------------------------------------------


def test_render_includes_pet_and_reasoning(owner, pet):
    tasks = [Task("Walk", 30, priority="high")]
    plan = build_plan(owner, pet, tasks)
    text = plan.render()
    assert "Mochi" in text
    assert "Walk" in text
    assert "priority: high" in text


def test_each_scheduled_task_has_a_reason(owner, pet):
    tasks = [
        Task("Vet", 30, priority="high", fixed_start="10:00"),
        Task("Walk", 30, priority="high"),
    ]
    plan = build_plan(owner, pet, tasks)
    assert all(s.reason for s in plan.scheduled)


def test_scheduler_class_and_helper_agree(owner, pet):
    tasks = [Task("Walk", 30, priority="high")]
    via_class = Scheduler().build_plan(owner, pet, tasks)
    via_helper = build_plan(owner, pet, tasks)
    assert via_class.render() == via_helper.render()

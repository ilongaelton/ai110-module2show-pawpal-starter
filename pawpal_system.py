"""PawPal+ core domain and scheduling logic.

This module is UI-agnostic: it knows nothing about Streamlit. The Streamlit
layer (``app.py``) and the test suite both build the same objects defined here
and call :meth:`Scheduler.build_plan` to produce a :class:`DailyPlan`.

Design overview (see ``diagrams/uml.mmd``):

    Owner 1 --- 1 Pet
    Owner 1 --- * Task
    Scheduler --> DailyPlan --> * ScheduledTask

The scheduler is a greedy planner. It places fixed-time tasks first, then
fills the owner's remaining time budget with the highest-priority flexible
tasks that still fit, recording a human-readable reason for every decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from itertools import count
from typing import Iterable, Optional


# Priority is an IntEnum so tasks sort naturally (HIGH > MEDIUM > LOW) and we
# can compare/weight without a lookup table.
class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def from_str(cls, value: "str | Priority") -> "Priority":
        """Accept the UI's lowercase strings ("high") or an existing Priority."""
        if isinstance(value, Priority):
            return value
        try:
            return cls[value.strip().upper()]
        except KeyError as exc:
            raise ValueError(
                f"Unknown priority {value!r}; expected one of "
                f"{[p.name.lower() for p in cls]}"
            ) from exc

    @property
    def label(self) -> str:
        return self.name.lower()


class Recurrence(IntEnum):
    """How often a task should appear. DAILY tasks are always candidates;
    WEEKLY tasks only appear on their ``weekday``."""

    DAILY = 0
    WEEKLY = 1

    @classmethod
    def from_str(cls, value: "str | Recurrence") -> "Recurrence":
        if isinstance(value, Recurrence):
            return value
        return cls[value.strip().upper()]


# Module-level counter so every Task gets a stable, unique id even when two
# tasks share the same title. Used to de-duplicate and to identify tasks in UI.
_task_ids = count(1)


def _fmt_time(minutes_from_midnight: int) -> str:
    """Render minutes-since-midnight as HH:MM (24h)."""
    minutes_from_midnight %= 24 * 60
    return f"{minutes_from_midnight // 60:02d}:{minutes_from_midnight % 60:02d}"


def _parse_time(value: str) -> int:
    """Parse 'HH:MM' into minutes since midnight."""
    hours, minutes = value.split(":")
    total = int(hours) * 60 + int(minutes)
    if not 0 <= total < 24 * 60:
        raise ValueError(f"Time {value!r} is outside 00:00–23:59")
    return total


@dataclass
class Task:
    """A single pet-care task.

    Attributes:
        title: Human-readable name ("Morning walk").
        duration_minutes: How long the task takes. Must be > 0.
        priority: Importance; drives ordering when time is scarce.
        fixed_start: Optional "HH:MM". If set, the task is an appointment that
            must happen at this time (e.g. a vet visit or medication window).
        recurrence: DAILY (default) or WEEKLY.
        weekday: For WEEKLY tasks, the Python weekday (0=Mon … 6=Sun) it runs.
    """

    title: str
    duration_minutes: int
    priority: Priority = Priority.MEDIUM
    fixed_start: Optional[str] = None
    recurrence: Recurrence = Recurrence.DAILY
    weekday: Optional[int] = None
    id: int = field(default_factory=lambda: next(_task_ids))

    def __post_init__(self) -> None:
        self.priority = Priority.from_str(self.priority)
        self.recurrence = Recurrence.from_str(self.recurrence)
        if self.duration_minutes <= 0:
            raise ValueError(
                f"Task {self.title!r} must have a positive duration, "
                f"got {self.duration_minutes}"
            )
        if self.fixed_start is not None:
            # Validate eagerly so bad input fails at construction, not planning.
            _parse_time(self.fixed_start)
        if self.recurrence is Recurrence.WEEKLY and self.weekday is None:
            raise ValueError(
                f"Weekly task {self.title!r} needs a weekday (0=Mon … 6=Sun)"
            )

    @property
    def is_fixed(self) -> bool:
        return self.fixed_start is not None

    @property
    def fixed_start_minutes(self) -> Optional[int]:
        return _parse_time(self.fixed_start) if self.fixed_start else None

    def occurs_on(self, weekday: int) -> bool:
        """Whether this task is a candidate on the given weekday."""
        if self.recurrence is Recurrence.DAILY:
            return True
        return self.weekday == weekday


@dataclass
class Pet:
    name: str
    species: str = "dog"
    breed: Optional[str] = None
    notes: Optional[str] = None

    @property
    def display(self) -> str:
        return f"{self.name} ({self.breed or self.species})"


@dataclass
class Owner:
    """The person planning the day.

    Attributes:
        name: Owner's name.
        available_minutes: Total time budget for pet care today.
        day_start: When the day's plan begins, "HH:MM".
        prefer_short_first: Tie-breaker preference. When two tasks share a
            priority, schedule the shorter one first (lets more tasks fit).
    """

    name: str
    available_minutes: int = 120
    day_start: str = "08:00"
    prefer_short_first: bool = True

    def __post_init__(self) -> None:
        if self.available_minutes < 0:
            raise ValueError("available_minutes cannot be negative")
        _parse_time(self.day_start)  # validate


@dataclass
class ScheduledTask:
    """A task placed on the timeline with a start/end and an explanation."""

    task: Task
    start_minutes: int
    reason: str

    @property
    def end_minutes(self) -> int:
        return self.start_minutes + self.task.duration_minutes

    @property
    def start(self) -> str:
        return _fmt_time(self.start_minutes)

    @property
    def end(self) -> str:
        return _fmt_time(self.end_minutes)

    def overlaps(self, other: "ScheduledTask") -> bool:
        return (
            self.start_minutes < other.end_minutes
            and other.start_minutes < self.end_minutes
        )

    def __str__(self) -> str:
        return (
            f"{self.start} — {self.task.title} "
            f"({self.task.duration_minutes} min) "
            f"[priority: {self.task.priority.label}]"
        )


@dataclass
class SkippedTask:
    task: Task
    reason: str


@dataclass
class DailyPlan:
    """The result of scheduling: what's in, what's out, and why."""

    pet: Pet
    owner: Owner
    scheduled: list[ScheduledTask] = field(default_factory=list)
    skipped: list[SkippedTask] = field(default_factory=list)

    @property
    def total_scheduled_minutes(self) -> int:
        return sum(s.task.duration_minutes for s in self.scheduled)

    @property
    def remaining_minutes(self) -> int:
        return self.owner.available_minutes - self.total_scheduled_minutes

    def render(self) -> str:
        """Render the plan as the README-style text block."""
        lines = [f"Daily plan for {self.pet.display}:"]
        if self.scheduled:
            for item in self.scheduled:
                lines.append(f"  {item}")
        else:
            lines.append("  (no tasks scheduled)")
        if self.skipped:
            lines.append("")
            lines.append("Skipped:")
            for skip in self.skipped:
                lines.append(f"  - {skip.task.title}: {skip.reason}")
        lines.append("")
        lines.append(
            f"Used {self.total_scheduled_minutes} of "
            f"{self.owner.available_minutes} min "
            f"({self.remaining_minutes} min free)."
        )
        return "\n".join(lines)


class Scheduler:
    """Greedy daily planner.

    Algorithm:
      1. Filter tasks down to those that occur on the target weekday.
      2. Place fixed-time (appointment) tasks first. If two appointments
         overlap, the lower-priority one is skipped (conflict handling).
      3. Sort the remaining flexible tasks by priority (high first), then by
         duration (owner preference), then by insertion order for stability.
      4. Greedily walk the day from ``day_start``, dropping each flexible task
         into the next gap that (a) fits before the next appointment and
         (b) stays within the owner's time budget. Tasks that can't fit are
         skipped with a reason ("ran out of time").
    """

    def build_plan(
        self,
        owner: Owner,
        pet: Pet,
        tasks: Iterable[Task],
        weekday: Optional[int] = None,
    ) -> DailyPlan:
        plan = DailyPlan(pet=pet, owner=owner)

        # 1. Recurrence filter.
        candidates = list(tasks)
        if weekday is not None:
            for task in candidates:
                if not task.occurs_on(weekday):
                    plan.skipped.append(
                        SkippedTask(task, f"not scheduled for this day")
                    )
            candidates = [t for t in candidates if t.occurs_on(weekday)]

        fixed = [t for t in candidates if t.is_fixed]
        flexible = [t for t in candidates if not t.is_fixed]

        budget = owner.available_minutes

        # 2. Place fixed appointments, highest priority first so that when two
        #    collide we keep the more important one.
        fixed.sort(key=lambda t: (-int(t.priority), t.fixed_start_minutes, t.id))
        for task in fixed:
            start = task.fixed_start_minutes
            candidate = ScheduledTask(
                task, start, reason=f"fixed appointment at {task.fixed_start}"
            )
            conflict = next(
                (s for s in plan.scheduled if s.overlaps(candidate)), None
            )
            if conflict is not None:
                plan.skipped.append(
                    SkippedTask(
                        task,
                        f"conflicts with '{conflict.task.title}' "
                        f"at {conflict.start}",
                    )
                )
                continue
            if task.duration_minutes > budget:
                plan.skipped.append(
                    SkippedTask(task, "not enough time budget for appointment")
                )
                continue
            plan.scheduled.append(candidate)
            budget -= task.duration_minutes

        # 3. Order flexible tasks.
        duration_key = 1 if owner.prefer_short_first else -1
        flexible.sort(
            key=lambda t: (-int(t.priority), duration_key * t.duration_minutes, t.id)
        )

        # 4. Greedy placement around the fixed blocks and within budget.
        cursor = _parse_time(owner.day_start)
        appointments = sorted(plan.scheduled, key=lambda s: s.start_minutes)
        for task in flexible:
            if task.duration_minutes > budget:
                plan.skipped.append(
                    SkippedTask(
                        task,
                        f"ran out of time ({budget} min left, "
                        f"needs {task.duration_minutes})",
                    )
                )
                continue
            start = self._next_free_slot(cursor, task.duration_minutes, appointments)
            placed = ScheduledTask(
                task,
                start,
                reason=(
                    f"priority {task.priority.label}; "
                    f"fit {task.duration_minutes} min into the day"
                ),
            )
            plan.scheduled.append(placed)
            appointments = sorted(
                appointments + [placed], key=lambda s: s.start_minutes
            )
            cursor = placed.end_minutes
            budget -= task.duration_minutes

        plan.scheduled.sort(key=lambda s: s.start_minutes)
        return plan

    @staticmethod
    def _next_free_slot(
        earliest: int, duration: int, busy: list[ScheduledTask]
    ) -> int:
        """Find the earliest start >= ``earliest`` where ``duration`` minutes
        fit without overlapping any block in ``busy``."""
        start = earliest
        # Walk forward past each conflicting block until we find a clear gap.
        moved = True
        while moved:
            moved = False
            for block in sorted(busy, key=lambda s: s.start_minutes):
                if start < block.end_minutes and block.start_minutes < start + duration:
                    start = block.end_minutes
                    moved = True
        return start


def build_plan(
    owner: Owner,
    pet: Pet,
    tasks: Iterable[Task],
    weekday: Optional[int] = None,
) -> DailyPlan:
    """Convenience wrapper so callers can avoid instantiating Scheduler."""
    return Scheduler().build_plan(owner, pet, tasks, weekday=weekday)

"""Recording-session statechart.

The chart is the gate the director runs. A beat exists only because the
``settling --capture--> captured`` edge fired. Failure states have no
outgoing edges, so a blocked session cannot pick up a beat later.

::

    idle      --start--> navigating
    captured  --start--> navigating
    navigating --pii-->        pii-blocked
    navigating --missing-->    selector-missing
    navigating --not-visible--> not-visible
    navigating --nav-error-->  nav-error
    navigating --invalid-->    invalid-value
    navigating --timeout-->    timeout
    navigating --act-->        acting
    acting    --pii-->         pii-blocked
    acting    --timeout-->     timeout
    acting    --settled-->     settling
    settling  --pii-->         pii-blocked
    settling  --timeout-->     timeout
    settling  --capture-->     captured     # only edge that adds a beat

Properties, turned into tests in tests/test_session.py:

1. Every step ends captured with exactly one new beat, or in a typed failure
   with the beat count unchanged.
2. A PII signal at any check (before the action, or again before capture)
   ends in ``pii-blocked``. Capture is not enabled from that state, so batch
   mode cannot bypass the guard by continuing the session.
3. The zoom-rect-inside-frame property lives next to ``compose.zoom_rect``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(str, Enum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    ACTING = "acting"
    SETTLING = "settling"
    CAPTURED = "captured"
    SELECTOR_MISSING = "selector-missing"
    NOT_VISIBLE = "not-visible"
    NAV_ERROR = "nav-error"
    INVALID_VALUE = "invalid-value"
    BLANK_PAGE = "blank-page"
    TIMEOUT = "timeout"
    PII_BLOCKED = "pii-blocked"


FAILURES = frozenset({
    Phase.SELECTOR_MISSING,
    Phase.NOT_VISIBLE,
    Phase.NAV_ERROR,
    Phase.INVALID_VALUE,
    Phase.BLANK_PAGE,
    Phase.TIMEOUT,
    Phase.PII_BLOCKED,
})

# Typed failures the director is allowed to raise. Anything else is a bug.
FAILURE_KINDS = frozenset(p.value for p in FAILURES)


class Event(str, Enum):
    START = "start"
    PII = "pii"
    MISSING = "missing"
    NOT_VISIBLE = "not-visible"
    NAV_ERROR = "nav-error"
    INVALID = "invalid-value"
    TIMEOUT = "timeout"
    ACT = "act"
    SETTLED = "settled"
    CAPTURE = "capture"


class InvalidTransition(RuntimeError):
    """A move the chart does not allow. This is a programmer error, not a take failure."""


class RecordingFailure(Exception):
    """One step failed.

    ``kind`` is selector-missing, not-visible, nav-error, invalid-value,
    blank-page, timeout, or pii-blocked. ``selector`` is the control that
    failed, when the step had one.
    """

    def __init__(self, kind: str, step_index: int = -1, detail: str = "", selector: str = "",
                 warning: str = ""):
        if kind not in FAILURE_KINDS:
            raise ValueError(f"untyped failure {kind!r}")
        self.kind = kind
        self.step_index = step_index
        self.detail = detail
        self.selector = selector or ""
        self.warning = warning or ""
        msg = f"{kind} at step {step_index}"
        if self.selector:
            msg = f"{msg} ({self.selector})"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


# (phase, event) -> (next phase, beats added)
# action_error values the chart can take before the action runs.
_ACTION_EVENTS = {
    "not-visible": Event.NOT_VISIBLE,
    "nav-error": Event.NAV_ERROR,
    "invalid-value": Event.INVALID,
    "selector-missing": Event.MISSING,
    "timeout": Event.TIMEOUT,
}


EDGES: dict[tuple[Phase, Event], tuple[Phase, int]] = {
    (Phase.IDLE, Event.START): (Phase.NAVIGATING, 0),
    (Phase.CAPTURED, Event.START): (Phase.NAVIGATING, 0),
    (Phase.NAVIGATING, Event.PII): (Phase.PII_BLOCKED, 0),
    (Phase.NAVIGATING, Event.MISSING): (Phase.SELECTOR_MISSING, 0),
    (Phase.NAVIGATING, Event.NOT_VISIBLE): (Phase.NOT_VISIBLE, 0),
    (Phase.NAVIGATING, Event.NAV_ERROR): (Phase.NAV_ERROR, 0),
    (Phase.NAVIGATING, Event.INVALID): (Phase.INVALID_VALUE, 0),
    (Phase.NAVIGATING, Event.TIMEOUT): (Phase.TIMEOUT, 0),
    (Phase.NAVIGATING, Event.ACT): (Phase.ACTING, 0),
    (Phase.ACTING, Event.PII): (Phase.PII_BLOCKED, 0),
    (Phase.ACTING, Event.TIMEOUT): (Phase.TIMEOUT, 0),
    (Phase.ACTING, Event.SETTLED): (Phase.SETTLING, 0),
    (Phase.SETTLING, Event.PII): (Phase.PII_BLOCKED, 0),
    (Phase.SETTLING, Event.TIMEOUT): (Phase.TIMEOUT, 0),
    (Phase.SETTLING, Event.CAPTURE): (Phase.CAPTURED, 1),
}


@dataclass(frozen=True)
class Session:
    phase: Phase
    beats: int = 0
    pii_seen: bool = False
    failure: str | None = None

    @staticmethod
    def idle() -> "Session":
        return Session(Phase.IDLE)


def transition(session: Session, event: Event) -> Session:
    """Follow one edge. Refuses every event once PII has been seen or a failure stuck."""
    if session.pii_seen or session.phase in FAILURES:
        raise InvalidTransition(
            f"{session.phase.value} refuses {event.value} after {session.failure or 'pii'}"
        )
    edge = EDGES.get((session.phase, event))
    if edge is None:
        raise InvalidTransition(f"{session.phase.value} refuses {event.value}")
    phase, delta = edge
    failure = phase.value if phase in FAILURES else None
    return Session(
        phase,
        session.beats + delta,
        session.pii_seen or phase is Phase.PII_BLOCKED,
        failure,
    )


@dataclass(frozen=True)
class StepSignals:
    """Observations for a single step.

    ``pii`` is the page before the action. ``pii_after`` is the page again
    before capture. Either one blocks. A found selector cannot clear them.
    """

    needs_selector: bool
    selector_found: bool
    timeout_at: str | None = None  # None | "navigating" | "acting" | "settling"
    pii: bool = False
    pii_after: bool = False
    # Set when the browser named the failure. Otherwise every exception
    # looked like a timeout. None | not-visible | nav-error | invalid-value
    # | selector-missing | timeout.
    action_error: str | None = None


def run_step(session: Session, signals: StepSignals) -> Session:
    """Run one step to completion.

    Returns captured with beats+1, or a typed failure with beats unchanged.
    Both PII observations are applied before timeout or capture, so a timeout
    cannot hide a hit and capture is unreachable once either flag is set.
    """
    session = transition(session, Event.START)
    if signals.pii or signals.pii_after:
        return transition(session, Event.PII)
    # A classified failure is itself the kind. Checking it here is what stops
    # a covered click or a refused navigation from collapsing into timeout.
    action = _ACTION_EVENTS.get(signals.action_error or "")
    if action is not None:
        return transition(session, action)
    if signals.timeout_at == "navigating":
        return transition(session, Event.TIMEOUT)
    if signals.needs_selector and not signals.selector_found:
        return transition(session, Event.MISSING)
    session = transition(session, Event.ACT)
    if signals.timeout_at == "acting":
        return transition(session, Event.TIMEOUT)
    session = transition(session, Event.SETTLED)
    if signals.timeout_at == "settling":
        return transition(session, Event.TIMEOUT)
    return transition(session, Event.CAPTURE)

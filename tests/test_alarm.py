"""Tests for the pure alarm logic: evaluate() and the Alarm debouncer."""

import pytest

from analog_level_sensor.alarm import Alarm, AlarmType, Breach, Direction, evaluate


class FakeClock:
    """A monotonic clock the tests drive by hand."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def make_alarm(grace_period=30.0, renotify_interval=900.0):
    alarm = Alarm(grace_period=grace_period, renotify_interval=renotify_interval)
    clock = FakeClock()
    # _now is a staticmethod, so an instance attribute shadows it cleanly.
    alarm._now = clock
    return alarm, clock


def test_evaluate_none_reading_is_silent():
    assert evaluate(None, AlarmType.greater_than, point=10) is None


def test_evaluate_unset_bound_is_silent():
    assert evaluate(50, AlarmType.greater_than, point=None) is None
    assert evaluate(50, AlarmType.less_than, point=None) is None
    assert evaluate(50, AlarmType.allowed_range, low=None, high=None) is None


def test_evaluate_greater_than():
    assert evaluate(11, AlarmType.greater_than, point=10) == Breach(
        Direction.exceeded, 10
    )
    assert evaluate(10, AlarmType.greater_than, point=10) is None
    assert evaluate(9, AlarmType.greater_than, point=10) is None


def test_evaluate_less_than():
    assert evaluate(9, AlarmType.less_than, point=10) == Breach(
        Direction.dropped_below, 10
    )
    assert evaluate(10, AlarmType.less_than, point=10) is None
    assert evaluate(11, AlarmType.less_than, point=10) is None


def test_evaluate_allowed_range_reports_the_crossed_bound():
    assert evaluate(4500, AlarmType.allowed_range, low=1000, high=4000) == Breach(
        Direction.exceeded, 4000
    )
    assert evaluate(300, AlarmType.allowed_range, low=1000, high=4000) == Breach(
        Direction.dropped_below, 1000
    )
    assert evaluate(2000, AlarmType.allowed_range, low=1000, high=4000) is None


def test_alarm_waits_out_the_grace_period():
    alarm, clock = make_alarm(grace_period=30.0)
    breach = Breach(Direction.exceeded, 10)

    assert alarm.update(breach) is False  # breach starts, grace begins
    clock.advance(29)
    assert alarm.update(breach) is False  # still inside the grace period
    clock.advance(1)
    assert alarm.update(breach) is True  # grace served
    assert alarm.active is True


def test_alarm_renotifies_on_interval():
    alarm, clock = make_alarm(grace_period=0.0, renotify_interval=900.0)
    breach = Breach(Direction.exceeded, 10)

    assert alarm.update(breach) is True
    clock.advance(899)
    assert alarm.update(breach) is False
    clock.advance(1)
    assert alarm.update(breach) is True


def test_clearing_rearms_a_full_grace_period():
    alarm, clock = make_alarm(grace_period=30.0)
    breach = Breach(Direction.exceeded, 10)

    clock.advance(30)
    assert alarm.update(breach) is False  # first sample starts the grace period
    clock.advance(30)
    assert alarm.update(breach) is True

    assert alarm.update(None) is False  # back in bounds
    assert alarm.active is False

    # the next breach must serve its own grace period, not notify instantly
    assert alarm.update(breach) is False
    clock.advance(30)
    assert alarm.update(breach) is True


def test_alarm_within_bounds_never_notifies():
    alarm, clock = make_alarm(grace_period=0.0)
    for _ in range(5):
        assert alarm.update(None) is False
        clock.advance(60)
    assert alarm.active is False


@pytest.mark.parametrize("grace", [0.0, 30.0])
def test_zero_and_nonzero_grace_both_debounce_a_flapping_reading(grace):
    alarm, clock = make_alarm(grace_period=grace, renotify_interval=900.0)
    breach = Breach(Direction.exceeded, 10)

    notifications = 0
    for _ in range(10):
        # flap in and out of bounds on every sample
        alarm.update(breach)
        if alarm.update(None):
            notifications += 1
        clock.advance(1)

    assert notifications == 0

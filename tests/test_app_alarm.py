"""Tests for the alarm's wiring into the device application.

These build a real config and inject a deployment config, so they also cover the
published key names and the fallbacks used when an older deployment config has
no alarm block at all.
"""

import pytest

from pydoover.config import NotSet

from common.common_app import CommonAnalogLevelSensorApplication

from analog_level_sensor.alarm import Alarm, AlarmType
from analog_level_sensor.app_config import AlarmSource, AnalogLevelSensorDeviceConfig
from analog_level_sensor.application import AnalogLevelSensorDeviceApplication as App


UNSET = object()

# 12 mA over a 4-20 mA span across 0-10 m: half scale.
MID_SCALE_MA = 12.0
MID_LEVEL_M = 5.0


def _reset(element):
    element._value = NotSet
    # Object holds its children in a dict, Array in a list.
    children = getattr(element, "_elements", None)
    if isinstance(children, dict):
        children = children.values()
    for child in children or ():
        _reset(child)


@pytest.fixture(autouse=True)
def reset_config_elements():
    """pydoover holds config elements as class attributes, shared by every
    instance, so an injected value outlives the config that injected it. A real
    app has one config; a test module has many."""
    for element in AnalogLevelSensorDeviceConfig()._element_map.values():
        _reset(element)


def make_config(alarm=UNSET, **overrides):
    """A fully injected device config. Pass alarm=UNSET to omit the block entirely."""
    data = {
        "ai_pin": 1,
        "sensor_maximum_metres": 10.0,
        "sensor_minimum_metres": 0.0,
        "full_level": 10.0,
        "empty_level": 0.0,
        "sensor_minimum_ma": 4.0,
        "sensor_maximum_ma": 20.0,
        "sensor_type": "Submersible",
        "volume_curve": [],
        "hide_volume": False,
        "max_volume": 1000.0,
        "volume_units": "L",
    }
    if alarm is not UNSET:
        data["alarm"] = alarm
    data.update(overrides)

    config = AnalogLevelSensorDeviceConfig()
    config._inject_deployment_config(data)
    return config


def enabled_alarm(**overrides):
    alarm = {"alarm_enabled": True, "alarm_type": "Greater Than"}
    alarm.update(overrides)
    return alarm


class StubSlider:
    def __init__(self, value=UNSET):
        self._value = value

    @property
    def value(self):
        # mirrors UICommandsManager.get_value() on an untouched, defaultless slider
        if self._value is UNSET:
            raise KeyError("slider has no stored value")
        return self._value


class StubUI:
    def __init__(self, point=UNSET, alarm_range=UNSET):
        self.alarm_point = StubSlider(point)
        self.alarm_range = StubSlider(alarm_range)


class StubApp(CommonAnalogLevelSensorApplication):
    """The real alarm methods, without an Application's device-agent connection."""

    app_display_name = "Tank"

    # re-wrap: accessing a staticmethod off the class unwraps the descriptor
    _slider_value = staticmethod(App._slider_value)
    _alarm_value = App._alarm_value
    _alarm_bounds = App._alarm_bounds
    _check_alarm = App._check_alarm
    _alarm_message = App._alarm_message

    def __init__(self, config, ui, alarm=None):
        self.config = config
        self.ui = ui
        self.alarm = alarm or Alarm(grace_period=0.0, renotify_interval=0.0)
        self.notifications = []

    async def send_notification(self, message, *, title=None, severity=None):
        self.notifications.append(message)


@pytest.mark.asyncio
async def test_untouched_slider_does_not_crash_or_notify():
    app = StubApp(make_config(alarm=enabled_alarm()), StubUI())

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []


@pytest.mark.asyncio
async def test_greater_than_message():
    app = StubApp(make_config(alarm=enabled_alarm()), StubUI(point=4.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_less_than_message():
    config = make_config(alarm=enabled_alarm(alarm_type="Less Than"))
    app = StubApp(config, StubUI(point=6.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has dropped below 6 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_allowed_range_reports_the_crossed_bound_upward():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    app = StubApp(config, StubUI(alarm_range=[1.0, 4.0]))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_allowed_range_reports_the_crossed_bound_downward():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    app = StubApp(config, StubUI(alarm_range=[6.0, 9.0]))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has dropped below 6 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_reversed_dual_slider_value_still_reads_correctly():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    app = StubApp(config, StubUI(alarm_range=[4.0, 1.0]))  # [high, low]

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_malformed_dual_slider_value_is_silent():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    app = StubApp(config, StubUI(alarm_range=4.0))  # a number, not a pair

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []


@pytest.mark.asyncio
async def test_reading_inside_the_range_is_silent():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    app = StubApp(config, StubUI(alarm_range=[1.0, 9.0]))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []


@pytest.mark.asyncio
async def test_disabled_alarm_never_notifies_even_on_a_breach():
    config = make_config(alarm=enabled_alarm(alarm_enabled=False))
    app = StubApp(config, StubUI(point=4.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []


@pytest.mark.asyncio
async def test_percentage_source_alarms_on_filled_percentage():
    config = make_config(alarm=enabled_alarm(alarm_source="Filled Percentage"))
    app = StubApp(config, StubUI(point=40.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 40 % with a value of 50 %"]


@pytest.mark.asyncio
async def test_volume_source_alarms_on_volume_in_configured_units():
    config = make_config(alarm=enabled_alarm(alarm_source="Volume"))
    app = StubApp(config, StubUI(point=400.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 400 L with a value of 500 L"]


def test_alarm_value_tracks_the_configured_source():
    ui = StubUI()
    for source, expected in [
        ("Level Reading", MID_LEVEL_M),
        ("Filled Percentage", 50.0),
        ("Volume", 500.0),
    ]:
        app = StubApp(make_config(alarm=enabled_alarm(alarm_source=source)), ui)
        assert app._alarm_value(MID_SCALE_MA) == expected


def test_slider_bounds_default_to_the_sources_own_span():
    level = make_config(alarm=enabled_alarm())
    assert (level.alarm_slider_min, level.alarm_slider_max) == (0.0, 10.0)
    assert level.alarm_units == "m"

    pct = make_config(alarm=enabled_alarm(alarm_source="Filled Percentage"))
    assert (pct.alarm_slider_min, pct.alarm_slider_max) == (0.0, 100.0)
    assert pct.alarm_units == "%"

    vol = make_config(alarm=enabled_alarm(alarm_source="Volume"))
    assert (vol.alarm_slider_min, vol.alarm_slider_max) == (0.0, 1000.0)
    assert vol.alarm_units == "L"


def test_explicit_slider_bounds_override_the_source_span():
    config = make_config(
        alarm=enabled_alarm(alarm_slider_minimum=2.0, alarm_slider_maximum=8.0)
    )
    assert (config.alarm_slider_min, config.alarm_slider_max) == (2.0, 8.0)


def test_volume_source_full_scale_comes_from_the_curve_when_present():
    config = make_config(
        alarm=enabled_alarm(alarm_source="Volume"),
        volume_curve=[
            {"level": 0.0, "volume": 0.0},
            {"level": 10.0, "volume": 7500.0},
        ],
    )
    assert config.alarm_slider_max == 7500.0


def test_alarm_type_normalises_a_raw_string_to_a_member():
    config = make_config(alarm=enabled_alarm(alarm_type="Allowed Range"))
    assert config.alarm_type is AlarmType.allowed_range
    assert config.alarm_source is AlarmSource.level


@pytest.mark.asyncio
async def test_deployment_config_without_an_alarm_block_reads_as_disabled():
    """An app install created before the alarm existed must keep working."""
    config = make_config()  # no alarm key at all

    assert config.alarm_enabled is False
    assert config.alarm_grace_period == 30.0
    assert config.alarm_renotify_interval == 900.0
    assert config.alarm_type is AlarmType.greater_than
    assert config.alarm_source is AlarmSource.level

    app = StubApp(config, StubUI(point=4.0))
    await app._check_alarm(MID_SCALE_MA)
    assert app.notifications == []

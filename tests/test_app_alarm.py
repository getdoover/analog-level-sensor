"""Tests for the alarm's wiring into the device application.

These build a real config and inject a deployment config, so they also cover the
published key names and the fallbacks used when an older deployment config
carries none of the alarm keys.
"""

import pytest
from pydoover.config import NotSet

from analog_level_sensor.alarm import Alarm, AlarmType
from analog_level_sensor.app_config import AlarmSource, AnalogLevelSensorDeviceConfig
from analog_level_sensor.app_ui import AnalogLevelSensorDeviceUI
from analog_level_sensor.application import AnalogLevelSensorDeviceApplication as App
from common.common_app import CommonAnalogLevelSensorApplication
from common.common_config import ReadingType

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
    """A fully injected device config.

    Pass alarm=UNSET to omit every alarm key, as a deployment config written
    before the alarm existed would.
    """
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
        data.update(alarm)
    data.update(overrides)

    config = AnalogLevelSensorDeviceConfig()
    config._inject_deployment_config(data)
    return config


def enabled_alarm(**overrides):
    """The alarm keys as they are published: flat, at the top level."""
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
    _format_value = staticmethod(App._format_value)
    _alarm_value = App._alarm_value
    _alarm_bounds = App._alarm_bounds
    _check_alarm = App._check_alarm
    _alarm_message = App._alarm_message

    def __init__(self, config, ui, alarm=None):
        self.config = config
        self.ui = ui
        self.alarm = alarm or Alarm(grace_period=0.0, renotify_interval=0.0)
        self.published = []

    @property
    def notifications(self):
        return [data["message"] for _channel, data in self.published]

    async def create_message(self, channel_name, data):
        self.published.append((channel_name, data))


@pytest.mark.asyncio
async def test_untouched_slider_does_not_crash_or_notify():
    app = StubApp(make_config(alarm=enabled_alarm()), StubUI())

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []


@pytest.mark.asyncio
async def test_greater_than_message():
    config = make_config(alarm=enabled_alarm(), reading_type="Level Reading")
    app = StubApp(config, StubUI(point=4.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_notification_payload_matches_the_data_plane_contract():
    """severity must be the serde variant name, not the int pydoover emits.

    An int fails to deserialise server-side, and the server then falls back to
    sending the whole JSON payload as the message body. Omitting the title makes
    the server substitute the agent's display name.
    """
    app = StubApp(make_config(alarm=enabled_alarm()), StubUI(point=4.0))

    await app._check_alarm(MID_SCALE_MA)

    channel, payload = app.published[0]
    assert channel == "notifications"
    assert payload["severity"] == "Warn"
    assert "title" not in payload
    assert set(payload) == {"message", "severity"}


@pytest.mark.asyncio
async def test_readings_are_rounded_to_two_decimal_places():
    # 12.8 mA -> 55.0% of a 4-20 mA span -> 5.5 m, and a bound of 1.23456 m
    config = make_config(
        alarm=enabled_alarm(alarm_type="Less Than"), reading_type="Level Reading"
    )
    app = StubApp(config, StubUI(point=6.98765))

    await app._check_alarm(12.8)

    assert app.notifications == ["Tank has dropped below 6.99 m with a value of 5.5 m"]


@pytest.mark.asyncio
async def test_less_than_message():
    config = make_config(
        alarm=enabled_alarm(alarm_type="Less Than"), reading_type="Level Reading"
    )
    app = StubApp(config, StubUI(point=6.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has dropped below 6 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_allowed_range_reports_the_crossed_bound_upward():
    config = make_config(
        alarm=enabled_alarm(alarm_type="Allowed Range"), reading_type="Level Reading"
    )
    app = StubApp(config, StubUI(alarm_range=[1.0, 4.0]))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_allowed_range_reports_the_crossed_bound_downward():
    config = make_config(
        alarm=enabled_alarm(alarm_type="Allowed Range"), reading_type="Level Reading"
    )
    app = StubApp(config, StubUI(alarm_range=[6.0, 9.0]))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has dropped below 6 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_reversed_dual_slider_value_still_reads_correctly():
    config = make_config(
        alarm=enabled_alarm(alarm_type="Allowed Range"), reading_type="Level Reading"
    )
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
    config = make_config(
        alarm=enabled_alarm(alarm_type="Allowed Range"), reading_type="Level Reading"
    )
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
    # An explicit null Reading Type reaches the deprecated Alarm Source fallback.
    config = make_config(
        alarm=enabled_alarm(alarm_source="Filled Percentage"), reading_type=None
    )
    app = StubApp(config, StubUI(point=40.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 40 % with a value of 50 %"]


@pytest.mark.asyncio
async def test_volume_source_alarms_on_volume_in_configured_units():
    # An explicit null Reading Type reaches the deprecated Alarm Source fallback.
    config = make_config(alarm=enabled_alarm(alarm_source="Volume"), reading_type=None)
    app = StubApp(config, StubUI(point=400.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 400 L with a value of 500 L"]


@pytest.mark.asyncio
async def test_unset_reading_type_keeps_the_legacy_alarm_source():
    """A legacy config carrying an explicit null Reading Type keeps its historic
    alarm source. (An omitted key instead resolves to the default and does not
    reach this fallback.)"""
    config = make_config(alarm=enabled_alarm(alarm_source="Volume"), reading_type=None)

    assert config.reading_type is None
    assert config.alarm_source is ReadingType.volume

    app = StubApp(config, StubUI(point=400.0))
    await app._check_alarm(MID_SCALE_MA)
    assert app.notifications == ["Tank has exceeded 400 L with a value of 500 L"]


@pytest.mark.asyncio
async def test_specific_reading_type_drives_the_alarm():
    config = make_config(alarm=enabled_alarm(), reading_type="Filled Percentage")
    app = StubApp(config, StubUI(point=40.0))

    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 40 % with a value of 50 %"]


def test_specific_reading_type_overrides_the_legacy_alarm_source():
    config = make_config(
        alarm=enabled_alarm(alarm_source="Volume"), reading_type="Level Reading"
    )
    assert config.alarm_source is ReadingType.level
    assert config.alarm_units == "m"


def test_reading_type_sets_the_slider_bounds_and_units():
    config = make_config(alarm=enabled_alarm(), reading_type="Volume")
    assert (config.alarm_slider_min, config.alarm_slider_max) == (0.0, 1000.0)
    assert config.alarm_units == "L"


def test_alarm_value_tracks_the_configured_source():
    ui = StubUI()
    for source, expected in [
        ("Level Reading", MID_LEVEL_M),
        ("Filled Percentage", 50.0),
        ("Volume", 500.0),
    ]:
        config = make_config(
            alarm=enabled_alarm(alarm_source=source), reading_type=None
        )
        app = StubApp(config, ui)
        assert app._alarm_value(MID_SCALE_MA) == expected


def test_slider_bounds_default_to_the_sources_own_span():
    # Explicit null Reading Type so the deprecated Alarm Source drives each span.
    level = make_config(alarm=enabled_alarm(), reading_type=None)
    assert (level.alarm_slider_min, level.alarm_slider_max) == (0.0, 10.0)
    assert level.alarm_units == "m"

    pct = make_config(
        alarm=enabled_alarm(alarm_source="Filled Percentage"), reading_type=None
    )
    assert (pct.alarm_slider_min, pct.alarm_slider_max) == (0.0, 100.0)
    assert pct.alarm_units == "%"

    vol = make_config(alarm=enabled_alarm(alarm_source="Volume"), reading_type=None)
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
        reading_type=None,
        volume_curve=[
            {"level": 0.0, "volume": 0.0},
            {"level": 10.0, "volume": 7500.0},
        ],
    )
    assert config.alarm_slider_max == 7500.0


def test_alarm_type_normalises_a_raw_string_to_a_member():
    config = make_config(
        alarm=enabled_alarm(alarm_type="Allowed Range"), reading_type=None
    )
    assert config.alarm_type is AlarmType.allowed_range
    # Explicit null Reading Type falls back to the Alarm Source default (level).
    assert config.alarm_source is AlarmSource.level


@pytest.mark.asyncio
async def test_deployment_config_without_alarm_keys_reads_as_disabled():
    """An app install created before the alarm existed must keep working."""
    config = make_config()  # none of the alarm keys are present

    assert config.alarm_enabled is False
    assert config.alarm_grace_period == 30.0
    assert config.alarm_renotify_interval == 900.0
    assert config.alarm_type is AlarmType.greater_than
    # With no reading_type key, Reading Type resolves to the "Filled Percentage"
    # default, so that (not the level default of the deprecated Alarm Source) is
    # the reported source. The alarm is disabled regardless.
    assert config.alarm_source is AlarmSource.percentage

    app = StubApp(config, StubUI(point=4.0))
    await app._check_alarm(MID_SCALE_MA)
    assert app.notifications == []


@pytest.mark.asyncio
async def test_level_alarm_compares_in_the_configured_depth_units():
    """With Depth Units mm the slider is set in mm, so the reading has to be
    converted before it is compared against it."""
    config = make_config(
        alarm=enabled_alarm(), reading_type="Level Reading", depth_units="mm"
    )

    # 12 mA -> 5 m -> 5000 mm, under the 6000 mm point
    quiet = StubApp(config, StubUI(point=6000.0))
    await quiet._check_alarm(MID_SCALE_MA)
    assert quiet.notifications == []

    # 16 mA -> 7.5 m -> 7500 mm, over it
    loud = StubApp(config, StubUI(point=6000.0))
    await loud._check_alarm(16.0)
    assert loud.notifications == ["Tank has exceeded 6000 mm with a value of 7500 mm"]


def test_level_slider_span_and_units_follow_the_depth_units():
    config = make_config(
        alarm=enabled_alarm(), reading_type="Level Reading", depth_units="mm"
    )

    assert config.alarm_units == "mm"
    assert (config.alarm_slider_min, config.alarm_slider_max) == (0.0, 10000.0)


def test_explicit_slider_bounds_are_taken_as_display_units():
    """Explicit bounds are in the units shown on the slider, so they are used
    as given rather than converted again."""
    config = make_config(
        alarm=enabled_alarm(alarm_slider_minimum=1000.0, alarm_slider_maximum=8000.0),
        reading_type="Level Reading",
        depth_units="mm",
    )

    assert (config.alarm_slider_min, config.alarm_slider_max) == (1000.0, 8000.0)


class FakeCommandsManager:
    """Enough of UICommandsManager for a Slider to read its stored value.

    The real manager raises KeyError for a key that has never been written,
    which is what an untouched (or freshly re-keyed) slider looks like.
    """

    def __init__(self, values):
        self.values = values

    def get_value(self, key):
        return self.values[key]


async def build_ui(config, stored):
    """The real device UI, bound to a stored set of ui_cmds values."""
    ui = AnalogLevelSensorDeviceUI(config, None, None)
    await ui.setup()
    manager = FakeCommandsManager(stored)
    for interaction in ui.get_interactions().values():
        interaction._manager = manager
    return ui


def mm_level_alarm(**overrides):
    return make_config(
        alarm=enabled_alarm(**overrides),
        reading_type="Level Reading",
        depth_units="mm",
    )


@pytest.mark.asyncio
async def test_a_metre_setpoint_is_not_reread_as_the_new_depth_unit():
    """The setpoint lives in ui_cmds, not the deployment config, so nothing in a
    deploy rescales it. An install that set 8.0 on the old 0-10 m slider and now
    shows millimetres must not have that 8.0 read as 8 mm -- a half-full tank
    would breach it forever."""
    config = mm_level_alarm()
    ui = await build_ui(config, {"alarm_point": 8.0})

    app = StubApp(config, ui)
    await app._check_alarm(MID_SCALE_MA)  # 5 m == 5000 mm

    assert app.notifications == []
    # the mm slider is a different element, so the metres value is left alone
    assert ui.alarm_point.name == "alarm_point_mm"
    assert ui.alarm_point.to_dict()["currentValue"] == "$cmds.app().alarm_point_mm"


@pytest.mark.asyncio
async def test_a_metre_range_is_not_reread_as_the_new_depth_unit():
    config = mm_level_alarm(alarm_type="Allowed Range")
    ui = await build_ui(config, {"alarm_range": [1.0, 4.0]})

    app = StubApp(config, ui)
    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == []
    assert ui.alarm_range.name == "alarm_range_mm"


@pytest.mark.asyncio
async def test_a_setpoint_set_in_the_configured_unit_alarms_on_that_scale():
    config = mm_level_alarm()
    ui = await build_ui(config, {"alarm_point_mm": 4000.0})

    app = StubApp(config, ui)
    await app._check_alarm(MID_SCALE_MA)

    assert app.notifications == ["Tank has exceeded 4000 mm with a value of 5000 mm"]


@pytest.mark.asyncio
async def test_a_metre_install_keeps_reading_its_existing_setpoint():
    """Metres are the scale the slider always read in, so the element key -- and
    with it every setpoint already stored against it -- is unchanged."""
    config = make_config(
        alarm=enabled_alarm(), reading_type="Level Reading", depth_units="m"
    )
    ui = await build_ui(config, {"alarm_point": 4.0})

    app = StubApp(config, ui)
    await app._check_alarm(MID_SCALE_MA)

    assert ui.alarm_point.name == "alarm_point"
    assert app.notifications == ["Tank has exceeded 4 m with a value of 5 m"]


@pytest.mark.asyncio
async def test_a_percentage_alarm_keeps_its_setpoint_whatever_the_depth_unit():
    """Depth Units does not rescale a percentage slider, so it must not re-key
    one either."""
    config = make_config(
        alarm=enabled_alarm(), reading_type="Filled Percentage", depth_units="mm"
    )
    ui = await build_ui(config, {"alarm_point": 40.0})

    app = StubApp(config, ui)
    await app._check_alarm(MID_SCALE_MA)

    assert ui.alarm_point.name == "alarm_point"
    assert app.notifications == ["Tank has exceeded 40 % with a value of 50 %"]

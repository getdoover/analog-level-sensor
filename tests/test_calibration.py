"""Operator Sensor Calibration (calibration.py) on the device app.

The app here is the real AnalogLevelSensorDeviceApplication with its device
agent connection replaced by in-memory stand-ins: ui_cmds is a dict the test
controls (``Harness.values``), the app's writes are recorded and only reach
ui_cmds when the test delivers their echo, and tags are a plain dict.
"""

import asyncio
import math

import pytest
from pydoover.config import NotSet
from pydoover.rpc import RPCError

from analog_level_sensor import calibration
from analog_level_sensor.alarm import Alarm, AlarmType, Direction, evaluate
from analog_level_sensor.app_config import AnalogLevelSensorDeviceConfig
from analog_level_sensor.app_tags import AnalogLevelSensorDeviceTags
from analog_level_sensor.app_ui import AnalogLevelSensorDeviceUI
from analog_level_sensor.application import AnalogLevelSensorDeviceApplication
from analog_level_sensor_processor.app_config import AnalogLevelSensorProcessorConfig
from analog_level_sensor_processor.app_tags import AnalogLevelSensorProcessorTags
from analog_level_sensor_processor.app_ui import AnalogLevelSensorProcessorUI
from common.common_app import CommonAnalogLevelSensorApplication

APP_KEY = "tank_level_1"
TAG_NAMES = [
    "level_filled_percentage",
    "level_reading",
    "level_reading_display",
    "raw_level_reading",
    "level_volume",
    "zero_m",
    "span_m",
    "fluid_density",
    "operator_calibration",
]
CALIBRATION_TAGS = ["zero_m", "span_m", "fluid_density", "operator_calibration"]


def _reset(element):
    element._value = NotSet
    children = getattr(element, "_elements", None)
    if isinstance(children, dict):
        children = children.values()
    for child in children or ():
        _reset(child)


@pytest.fixture(autouse=True)
def reset_config_elements():
    """pydoover config elements are class attributes shared by every instance."""
    for element in AnalogLevelSensorDeviceConfig()._element_map.values():
        _reset(element)


def make_config(enabled=None, **overrides):
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
    if enabled is not None:
        data["operator_calibration_enabled"] = enabled
    data.update(overrides)
    config = AnalogLevelSensorDeviceConfig()
    config._inject_deployment_config(data)
    return config


class FakeTag:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def get(self):
        return self._store.get(self._name)

    async def set(self, value, log=False):
        self._store[self._name] = value


class FakeTags:
    def __init__(self, persisted=None):
        self.store = dict(persisted or {})
        self.writes = []

    def __getitem__(self, name):
        tag = FakeTag(self.store, name)
        original = tag.set

        async def recording_set(value, log=False):
            self.writes.append((name, value))
            await original(value, log)

        tag.set = recording_set
        return tag

    def __getattr__(self, name):
        if name in TAG_NAMES:
            return self[name]
        raise AttributeError(name)


class FakeUIManager:
    app_key = APP_KEY

    def __init__(self, values=None):
        self.values = dict(values or {})


class FakeDeviceAgent:
    def __init__(self, synced):
        self.synced = synced

    async def wait_for_channels_sync(self, channels, timeout, inter_wait):
        return self.synced


class Harness(AnalogLevelSensorDeviceApplication):
    """The real app, minus pydoover's __init__ (no device agent)."""

    def __init__(self, config, values=None, synced=True, persisted=None):
        self.config = config
        self.tags = FakeTags(persisted)
        # ui_cmds as the app sees it (UICommandsManager.values); None = not
        # delivered yet (pydoover leaves it an empty dict until it syncs).
        self.ui_manager = FakeUIManager(values)
        self.device_agent = FakeDeviceAgent(synced)
        self.alarm = Alarm()
        self.written = []  # ui_cmds aggregate writes not yet echoed
        self.aggregate_writes = []

    async def update_channel_aggregate(self, channel_name, data, **kwargs):
        assert channel_name == "ui_cmds"
        self.aggregate_writes.append(data[APP_KEY])
        self.written.append(data[APP_KEY])

    def echo(self, count=None):
        """Deliver the next *count* writes (all by default) into ui_cmds, as
        the aggregate would: a deep merge, where None deletes a key."""
        count = len(self.written) if count is None else count
        for payload in self.written[:count]:
            for key, value in payload.items():
                if value is None:
                    self.ui_manager.values.pop(key, None)
                else:
                    self.ui_manager.values[key] = value
        del self.written[:count]

    async def start(self):
        # The part of setup() this feature adds (no platform interface here).
        self.calibration = calibration.SensorCalibration(self)
        await self.calibration.startup()


def run(coro):
    return asyncio.run(coro)


async def started(config, **kwargs) -> Harness:
    app = Harness(config, **kwargs)
    await app.start()
    return app


class PlainApp(CommonAnalogLevelSensorApplication):
    """Today's conversion: the shared code on the config alone."""

    def __init__(self, config):
        self.config = config
        self.tags = FakeTags()


READINGS = [4.0, 6.5, 12.0, 17.3, 20.0, 21.0]
TYPES = ["Submersible", "Radar", "Radar Inverted"]


# -- default off: identical to today ------------------------------------------------


def test_config_flag_defaults_off():
    assert make_config().operator_calibration_enabled is False
    assert make_config(enabled=True).operator_calibration_enabled is True


@pytest.mark.parametrize("sensor_type", TYPES)
@pytest.mark.parametrize("density", [None, 1250.0])
def test_off_readings_identical_to_today(sensor_type, density):
    overrides = {"sensor_type": sensor_type, "sensor_minimum_metres": 0.2}
    if density is not None:
        overrides["fluid_density"] = density
    config = make_config(**overrides)
    # Operator values sitting in ui_cmds (from a time it was on) are ignored.
    app = run(
        started(config, values={"zero_m": 1.0, "span_m": 3.0, "fluid_density": 900})
    )
    plain = PlainApp(config)
    for reading in READINGS:
        assert app._level_reading(reading) == plain._level_reading(reading)
        assert app._filled_percentage(reading) == plain._filled_percentage(reading)
        assert app._volume(reading) == plain._volume(reading)


def test_off_publishes_no_calibration_tags_and_writes_nothing():
    app = run(started(make_config()))

    async def loop():
        await app.calibration.maintain()
        await app.handle_update(12.0)

    run(loop())
    assert all(name not in CALIBRATION_TAGS for name, _ in app.tags.writes)
    assert all(app.tags.store.get(n) is None for n in CALIBRATION_TAGS)
    assert app.aggregate_writes == []


def test_off_clears_tags_left_by_an_earlier_enabled_run():
    persisted = {
        "zero_m": 0.5,
        "span_m": 4.0,
        "fluid_density": 1100.0,
        "operator_calibration": True,
    }
    app = run(started(make_config(enabled=False), persisted=persisted))
    assert all(app.tags.store[n] is None for n in CALIBRATION_TAGS)


@pytest.mark.parametrize("method", ["zero_m", "span_m", "fluid_density"])
def test_off_refuses_the_rpcs(method):
    app = run(started(make_config()))
    with pytest.raises(RPCError) as err:
        run(app.calibration.request(method, 1.0))
    assert err.value.code == "UNAVAILABLE"
    with pytest.raises(RPCError) as err:
        run(app.calibration.reset())
    assert err.value.code == "UNAVAILABLE"
    assert app.aggregate_writes == []


def build_ui(config):
    ui = AnalogLevelSensorDeviceUI(config, None, None)
    run(ui.setup())
    return ui


def test_off_hides_the_submodule():
    assert build_ui(make_config()).sensor_calibration.hidden is True


def test_on_shows_the_submodule_with_config_defaults():
    ui = build_ui(
        make_config(
            enabled=True,
            sensor_minimum_metres=0.25,
            sensor_maximum_metres=6.0,
            fluid_density=1180.0,
        )
    )
    sub = ui.sensor_calibration
    assert sub.hidden is False
    assert sub.zero_m.default == 0.25
    assert sub.span_m.default == 6.0
    assert sub.fluid_density.default == 1180.0
    assert sub.zero_m.to_dict()["currentValue"] == "$cmds.app().zero_m::0.25"
    assert sub.zero_m.display_name == "Zero - level at 4 mA (m)"
    assert sub.span_m.display_name == "Span - level at 20 mA (m)"
    assert sub.reset_calibration.display_name == "Reset to configured values"


@pytest.mark.parametrize(
    "sensor_type,zero_at,span_at",
    [("Submersible", 4, 20), ("Radar", 20, 4), ("Radar Inverted", 4, 20)],
)
def test_labels_name_the_input_each_value_is_read_at(sensor_type, zero_at, span_at):
    """A Radar reads inverted: its zero (minimum level) is at 20 mA."""
    sub = build_ui(
        make_config(enabled=True, sensor_type=sensor_type)
    ).sensor_calibration
    assert sub.zero_m.display_name == f"Zero - level at {zero_at} mA (m)"
    assert sub.span_m.display_name == f"Span - level at {span_at} mA (m)"

    app = run(started(make_config(enabled=True, sensor_type=sensor_type)))
    run(app.calibration.request("zero_m", 1.0))
    run(app.calibration.request("span_m", 5.0))
    assert app._level_reading(float(zero_at)) == pytest.approx(1.0)
    assert app._level_reading(float(span_at)) == pytest.approx(5.0)


def test_on_density_input_defaults_to_water_without_a_config_density():
    ui = build_ui(make_config(enabled=True, fluid_density=None))
    assert ui.sensor_calibration.fluid_density.default == 1000.0


# -- enabled: values drive every derived reading -------------------------------------


def test_on_without_operator_values_matches_the_config():
    config = make_config(enabled=True, fluid_density=1250.0)
    app = run(started(config))
    plain = PlainApp(config)
    for reading in READINGS:
        assert app._level_reading(reading) == plain._level_reading(reading)
    assert app.tags.store["zero_m"] == 0.0
    assert app.tags.store["span_m"] == 10.0
    assert app.tags.store["fluid_density"] == 1250.0
    assert app.tags.store["operator_calibration"] is True


def test_zero_and_span_change_level_and_percentage():
    app = run(started(make_config(enabled=True)))
    assert run(app.calibration.request("zero_m", 1.0)) == 1.0
    assert run(app.calibration.request("span_m", 5.0)) == 5.0

    # 12 mA = half scale: 1 m + (5 - 1) / 2 = 3 m; empty 0 / full 10 -> 30 %.
    assert app._level_reading(12.0) == pytest.approx(3.0)
    assert app._filled_percentage(12.0) == pytest.approx(30.0)
    assert app._volume(12.0) == pytest.approx(300.0)

    run(app.handle_update(12.0))
    assert app.tags.store["level_reading"] == pytest.approx(3.0)
    assert app.tags.store["level_filled_percentage"] == pytest.approx(30.0)
    assert app.tags.store["zero_m"] == 1.0
    assert app.tags.store["span_m"] == 5.0


def test_density_scales_the_submersible_column_above_the_zero():
    app = run(started(make_config(enabled=True)))
    run(app.calibration.request("zero_m", 0.5))
    run(app.calibration.request("span_m", 10.5))
    run(app.calibration.request("fluid_density", 1250))

    # 0.5 m + (5 m of water column / SG 1.25) = 4.5 m.
    assert app._level_reading(12.0) == pytest.approx(4.5)
    assert app._filled_percentage(12.0) == pytest.approx(45.0)
    assert app.tags.store["fluid_density"] == 1250.0


def test_radar_ignores_density_but_follows_zero_and_span():
    config = make_config(enabled=True, sensor_type="Radar")
    app = run(started(config))
    run(app.calibration.request("span_m", 4.0))
    run(app.calibration.request("fluid_density", 1500))
    # Same mapping as the config fields: set the config to the same values.
    plain = PlainApp(make_config(sensor_type="Radar", sensor_maximum_metres=4.0))
    for reading in READINGS:
        assert app._level_reading(reading) == pytest.approx(
            plain._level_reading(reading)
        )


def test_level_alarm_follows_the_calibration():
    config = make_config(enabled=True, reading_type="Level Reading")
    app = run(started(config))
    run(app.calibration.request("span_m", 5.0))
    assert app._alarm_value(12.0) == pytest.approx(2.5)


def test_change_applies_on_the_next_reading_without_restart():
    app = run(started(make_config(enabled=True)))
    run(app.handle_update(12.0))
    assert app.tags.store["level_reading"] == pytest.approx(5.0)
    run(app.calibration.request("span_m", 8.0))
    run(app.handle_update(12.0))
    assert app.tags.store["level_reading"] == pytest.approx(4.0)


# -- validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,value",
    [
        ("zero_m", -100.01),
        ("zero_m", -100.00004),  # rounds to -100 but is below it
        ("zero_m", 10.0),  # not below the 10 m span
        ("zero_m", 100.0),
        ("span_m", 0.0),
        ("span_m", -0.5),  # the maximum level stays above the datum
        ("span_m", 100.01),
        ("span_m", 100.00004),  # rounds to 100 but is above it
        ("span_m", 0.00004),  # above 0 but would be stored as 0
        ("fluid_density", 499.9),
        ("fluid_density", 2500.1),
        ("zero_m", "abc"),
        ("span_m", None),
        ("span_m", True),
        ("fluid_density", {"value": 1000}),
        ("zero_m", math.nan),
        ("span_m", math.inf),
    ],
)
def test_invalid_values_are_refused_and_leave_the_value(method, value):
    app = run(started(make_config(enabled=True)))
    before = app.calibration.effective()
    with pytest.raises(RPCError) as err:
        run(app.calibration.request(method, value))
    assert err.value.code == "INVALID"
    assert app.calibration.effective() == before
    assert app.aggregate_writes == []


def test_span_must_stay_above_an_operator_zero():
    app = run(started(make_config(enabled=True)))
    run(app.calibration.request("zero_m", 3.0))
    with pytest.raises(RPCError) as err:
        run(app.calibration.request("span_m", 3.0))
    assert err.value.code == "INVALID"
    assert app.calibration.effective().span_m == 10.0


def test_boundaries_and_rounding():
    app = run(started(make_config(enabled=True)))
    assert run(app.calibration.request("span_m", 100)) == 100.0
    assert run(app.calibration.request("zero_m", 0)) == 0.0
    assert run(app.calibration.request("fluid_density", 500)) == 500.0
    assert run(app.calibration.request("fluid_density", 2500)) == 2500.0
    assert run(app.calibration.request("zero_m", "0.123456")) == 0.1235
    value = run(app.calibration.request("span_m", 7.25))
    assert isinstance(value, float) and value == 7.25
    zero = run(app.calibration.request("zero_m", -0.0))
    assert zero == 0.0 and math.copysign(1, zero) == 1  # stored as 0, not -0
    zero = run(app.calibration.request("zero_m", -0.00004))
    assert zero == 0.0 and math.copysign(1, zero) == 1  # rounds to 0, not -0
    assert run(app.calibration.request("zero_m", -100)) == -100.0
    assert run(app.calibration.request("zero_m", "-0.15")) == -0.15


# -- a negative zero (the minimum level below the tank datum) ------------------------


def test_negative_zero_reads_below_the_datum_and_derived_values_follow():
    """A transmitter whose 4 mA point is 0.15 m below the tank floor."""
    app = run(started(make_config(enabled=True)))
    assert run(app.calibration.request("zero_m", -0.15)) == -0.15
    assert run(app.calibration.request("span_m", 2.0)) == 2.0

    # 4 mA is the zero: -0.15 m. Empty 0 / full 10 m, max volume 1000 L: the
    # percentage and volume follow the level linearly, unclamped (as for any
    # level below the empty level), so they read just below 0 there.
    assert app._level_reading(4.0) == pytest.approx(-0.15)
    assert app._filled_percentage(4.0) == pytest.approx(-1.5)
    assert app._volume(4.0) == pytest.approx(-15.0)
    # 0 m is crossed at 4 + 16 * 0.15 / 2.15 mA; 12 mA = -0.15 + 2.15 / 2.
    assert app._level_reading(4 + 16 * 0.15 / 2.15) == pytest.approx(0.0)
    assert app._level_reading(12.0) == pytest.approx(0.925)
    assert app._filled_percentage(12.0) == pytest.approx(9.25)
    assert app._level_reading(20.0) == pytest.approx(2.0)

    run(app.handle_update(4.0))
    assert app.tags.store["level_reading"] == pytest.approx(-0.15)
    assert app.tags.store["level_reading_display"] == pytest.approx(-0.15)
    assert app.tags.store["level_filled_percentage"] == pytest.approx(-1.5)
    assert app.tags.store["level_volume"] == pytest.approx(-15.0)
    assert app.tags.store["zero_m"] == -0.15
    assert app.tags.store["span_m"] == 2.0


def test_negative_zero_with_a_volume_curve_extrapolates_the_first_segment():
    curve = [{"level": 0.0, "volume": 0.0}, {"level": 2.0, "volume": 400.0}]
    app = run(started(make_config(enabled=True, volume_curve=curve)))
    run(app.calibration.request("zero_m", -0.15))
    run(app.calibration.request("span_m", 2.0))
    assert app._volume(4.0) == pytest.approx(-30.0)
    assert app._filled_percentage(4.0) == pytest.approx(-7.5)


def test_negative_zero_density_scales_only_the_column_above_it():
    app = run(started(make_config(enabled=True)))
    run(app.calibration.request("zero_m", -0.5))
    run(app.calibration.request("span_m", 9.5))
    run(app.calibration.request("fluid_density", 1250))
    # 4 mA is the zero whatever the fluid; 12 mA: -0.5 + 5 m of water / 1.25.
    assert app._level_reading(4.0) == pytest.approx(-0.5)
    assert app._level_reading(12.0) == pytest.approx(3.5)


def test_negative_zero_on_a_radar_is_the_level_at_20_ma():
    """A Radar reads inverted: its zero (the minimum level) is at 20 mA and
    its span, the level at 4 mA, stays above 0."""
    app = run(started(make_config(enabled=True, sensor_type="Radar")))
    run(app.calibration.request("zero_m", -0.2))
    run(app.calibration.request("span_m", 3.0))
    assert app._level_reading(20.0) == pytest.approx(-0.2)
    assert app._level_reading(4.0) == pytest.approx(3.0)
    with pytest.raises(RPCError) as err:
        run(app.calibration.request("span_m", -0.1))
    assert err.value.code == "INVALID"


def test_negative_zero_level_alarm():
    """A level alarm compares the negative level as it is (a Less Than
    alarm below the empty tank fires); a percentage alarm sees -1.5 %."""
    app = run(started(make_config(enabled=True, reading_type="Level Reading")))
    run(app.calibration.request("zero_m", -0.15))
    reading = app._alarm_value(4.0)
    assert reading == pytest.approx(-0.15)
    breach = evaluate(reading, AlarmType.less_than, point=0.1)
    assert breach == (Direction.dropped_below, 0.1)
    assert app._format_value(reading) == "-0.15"
    app = run(started(make_config(enabled=True, reading_type="Filled Percentage")))
    run(app.calibration.request("zero_m", -0.15))
    run(app.calibration.request("span_m", 2.0))
    assert app._alarm_value(4.0) == pytest.approx(-1.5)


def test_span_must_stay_above_a_negative_zero():
    app = run(started(make_config(enabled=True)))
    run(app.calibration.request("zero_m", -0.5))
    with pytest.raises(RPCError) as err:
        run(app.calibration.request("span_m", 0.0))
    assert err.value.code == "INVALID"
    assert run(app.calibration.request("span_m", 0.0001)) == 0.0001
    with pytest.raises(RPCError) as err:
        run(app.calibration.request("zero_m", 0.0001))
    assert "below the span" in err.value.message


def test_cloud_zero_input_allows_a_negative_value():
    sub = build_ui(make_config(enabled=True)).sensor_calibration
    assert sub.zero_m.to_dict()["min"] == -100.0
    assert sub.span_m.to_dict()["min"] == 0.0
    assert calibration.ZERO.describe_range() == "-100 to below 100 m"


def test_negative_config_zero_is_the_default_and_restored():
    config = make_config(enabled=True, sensor_minimum_metres=-0.15)
    app = run(started(config))
    assert app.calibration.effective().zero_m == -0.15
    assert app.tags.store["zero_m"] == -0.15
    assert build_ui(config).sensor_calibration.zero_m.default == -0.15

    persisted = {"zero_m": -0.3, "span_m": 4.5, "fluid_density": 1000.0}
    app = run(started(make_config(enabled=True), synced=False, persisted=persisted))
    assert app.calibration.effective() == (-0.3, 4.5, 1000.0)


class FailingWrites(Harness):
    """ui_cmds cannot be written (the DDA is down) until ``online`` is set."""

    online = False

    async def update_channel_aggregate(self, channel_name, data, **kwargs):
        if not self.online:
            raise ConnectionError("DDA down")
        await super().update_channel_aggregate(channel_name, data, **kwargs)


def test_a_failed_write_changes_nothing_and_reports_it():
    app = FailingWrites(make_config(enabled=True), values={"span_m": 6.0})
    run(app.start())
    before = app.calibration.effective()
    tags = dict(app.tags.store)

    for call in (
        app.calibration.request("span_m", 8.0),
        app.calibration.request("zero_m", 1.0),
        app.calibration.reset(),
    ):
        with pytest.raises(RPCError) as err:
            run(call)
        assert err.value.code == "UNAVAILABLE"
        assert app.calibration.effective() == before
        run(app.calibration.maintain())  # publishes the readback tags
        assert app.tags.store == tags

    # Back online: nothing from the failed writes lingers.
    app.online = True
    run(app.calibration.request("span_m", 7.0))
    app.echo()
    assert app.calibration.effective().span_m == 7.0
    run(app.calibration.maintain())
    assert app.aggregate_writes == [{"span_m": 7.0, "span_m_default": None}]


def test_rpc_handler_returns_the_applied_value():
    app = run(started(make_config(enabled=True)))

    class Ctx:
        method = "span_m"

    assert run(app.on_calibration_value(Ctx(), 6.5)) == {"span_m": 6.5}


# -- ui_cmds persistence, echoes and reset --------------------------------------------


def test_write_goes_to_ui_cmds_and_the_echo_keeps_it():
    app = run(started(make_config(enabled=True), values={"alarm_point": 3}))
    run(app.calibration.request("span_m", 6.0))
    assert app.aggregate_writes == [{"span_m": 6.0, "span_m_default": None}]
    app.echo()
    assert app.calibration.effective().span_m == 6.0


def test_two_quick_writes_the_first_echo_does_not_revert():
    app = run(started(make_config(enabled=True), values={"alarm_point": 3}))
    run(app.calibration.request("span_m", 6.0))
    run(app.calibration.request("span_m", 7.0))
    app.echo(1)
    assert app.calibration.effective().span_m == 7.0
    app.echo()
    assert app.calibration.effective().span_m == 7.0


def test_direct_ui_cmds_edit_is_adopted_and_invalid_one_ignored():
    app = run(started(make_config(enabled=True), values={"span_m": 6.0}))
    assert app.calibration.effective().span_m == 6.0
    app.ui_manager.values["span_m"] = 7.5
    assert app.calibration.effective().span_m == 7.5
    app.ui_manager.values["span_m"] = 250
    assert app.calibration.effective().span_m == 7.5


def test_inverted_direct_edit_falls_back_to_the_config_pair():
    app = run(started(make_config(enabled=True), values={"zero_m": 6.0, "span_m": 5.0}))
    effective = app.calibration.effective()
    assert (effective.zero_m, effective.span_m) == (0.0, 10.0)


def test_reset_writes_the_config_defaults_and_clears_the_operator_values():
    config = make_config(enabled=True, sensor_maximum_metres=8.0, fluid_density=1100)
    app = run(started(config, values={"zero_m": 1.0, "span_m": 6.0}))
    assert app.calibration.effective().zero_m == 1.0

    result = run(app.on_reset_calibration(None, {}))
    assert result == {"zero_m": 0.0, "span_m": 8.0, "fluid_density": 1100.0}
    assert app.aggregate_writes == [
        {
            "zero_m": 0.0,
            "zero_m_default": 0.0,
            "span_m": 8.0,
            "span_m_default": 8.0,
            "fluid_density": 1100.0,
            "fluid_density_default": 1100.0,
        }
    ]
    assert app.tags.store["span_m"] == 8.0
    app.echo()
    assert app.calibration.effective() == (0.0, 8.0, 1100.0)
    assert all(s.setting() is None for s in app.calibration.values.values())


def test_after_reset_a_config_change_takes_effect_and_is_written_back():
    values = {"span_m": 8.0, "span_m_default": 8.0, "zero_m": 0.0}
    # The config span was 8 m at the reset; it is now 9 m.
    app = run(
        started(make_config(enabled=True, sensor_maximum_metres=9.0), values=values)
    )
    assert app.calibration.effective().span_m == 9.0
    run(app.calibration.maintain())
    assert app.aggregate_writes == [{"span_m": 9.0, "span_m_default": 9.0}]
    app.echo()
    run(app.calibration.maintain())
    assert len(app.aggregate_writes) == 1
    assert app.calibration.effective().span_m == 9.0


def test_operator_value_after_a_reset_clears_the_marker():
    app = run(started(make_config(enabled=True), values={"alarm_point": 1}))
    run(app.calibration.reset())
    app.echo()
    run(app.calibration.request("span_m", 10.0))  # same number as the default
    app.echo()
    assert "span_m_default" not in app.ui_manager.values
    assert app.calibration.values["span_m"].setting() == 10.0


# -- restart and offline boot ----------------------------------------------------------


def test_restart_adopts_the_ui_cmds_values():
    values = {"zero_m": 0.5, "span_m": 4.5, "fluid_density": 1200.0}
    app = run(started(make_config(enabled=True), values=values))
    assert app.calibration.effective() == (0.5, 4.5, 1200.0)
    assert app.tags.store["zero_m"] == 0.5
    assert app.tags.store["fluid_density"] == 1200.0
    # 0.5 + (2 m water column / SG 1.2) at 12 mA
    assert app._level_reading(12.0) == pytest.approx(0.5 + 2.0 / 1.2)
    assert app.aggregate_writes == []


def test_offline_boot_restores_from_the_tags_until_ui_cmds_delivers():
    persisted = {"zero_m": 0.5, "span_m": 4.5, "fluid_density": 1000.0}
    app = run(started(make_config(enabled=True), synced=False, persisted=persisted))
    # fluid_density equals the config default: nothing to restore for it.
    assert app.calibration.effective() == (0.5, 4.5, 1000.0)
    assert app.calibration.values["fluid_density"].setting() is None
    assert app.tags.store["span_m"] == 4.5

    # ui_cmds then delivers the operator values (a later HMI edit made offline
    # elsewhere would win the same way).
    app.ui_manager.values.update({"zero_m": 0.5, "span_m": 5.0})
    assert app.calibration.effective() == (0.5, 5.0, 1000.0)


def test_offline_restore_dropped_when_ui_cmds_has_no_values():
    persisted = {"zero_m": 0.5, "span_m": 4.5}
    app = run(started(make_config(enabled=True), synced=False, persisted=persisted))
    assert app.calibration.effective().span_m == 4.5
    app.ui_manager.values.update({"alarm_point": 2.0})
    assert app.calibration.effective() == (0.0, 10.0, 1000.0)


def test_offline_restore_ignores_invalid_tags():
    persisted = {"zero_m": -150, "span_m": "x", "fluid_density": 9000}
    app = run(started(make_config(enabled=True), synced=False, persisted=persisted))
    assert app.calibration.effective() == (0.0, 10.0, 1000.0)


def test_write_before_a_late_sync_wins_and_is_stored_again():
    app = run(started(make_config(enabled=True), synced=False))
    run(app.calibration.request("span_m", 6.0))
    app.written.clear()  # that write was lost while offline
    # ui_cmds now syncs with an older value.
    app.ui_manager.values.update({"span_m": 4.0})
    assert app.calibration.effective().span_m == 6.0
    run(app.calibration.maintain())
    assert app.aggregate_writes[-1] == {"span_m": 6.0, "span_m_default": None}
    app.echo()
    assert app.calibration.effective().span_m == 6.0


# -- live loop current for the HMI Sensor tab --------------------------------------------


@pytest.mark.parametrize("reading", [3.9, 0.0])
def test_under_range_loop_current_is_published_when_on(reading):
    app = run(started(make_config(enabled=True)))
    run(app.handle_update(12.0))
    level = app.tags.store["level_reading"]
    run(app.handle_update(reading))
    assert app.tags.store["raw_level_reading"] == reading
    # Nothing derived from the untrusted sample.
    assert app.tags.store["level_reading"] == level


def test_under_range_loop_current_is_dropped_when_off():
    app = run(started(make_config()))
    run(app.handle_update(12.0))
    run(app.handle_update(3.9))
    assert app.tags.store["raw_level_reading"] == 12.0


# -- the processor variant ---------------------------------------------------------------


def test_processor_keeps_the_config_conversion():
    """The processor variant has no operator calibration: no config field, no
    readback tags, no submodule, and the shared conversion on the config."""
    assert not hasattr(
        AnalogLevelSensorProcessorConfig, "_operator_calibration_enabled"
    )
    assert not hasattr(AnalogLevelSensorProcessorTags, "zero_m")
    assert not hasattr(AnalogLevelSensorProcessorUI, "sensor_calibration")


def test_device_tags_declare_the_readbacks_live():
    for name in CALIBRATION_TAGS:
        assert getattr(AnalogLevelSensorDeviceTags, name).live is True

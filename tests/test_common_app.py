import logging

import pytest

from common.common_app import FAULT_DEBOUNCE_SAMPLES, CommonAnalogLevelSensorApplication
from common.common_config import (
    CommonAnalogLevelSensorConfig,
    DepthUnits,
    SensorType,
)


class Value:
    def __init__(self, value):
        self.value = value


class VolumeCurve:
    def __init__(self):
        self.elements = []


class FakeConfig:
    sensor_min_mA = Value(4.0)
    sensor_max_mA = Value(20.0)
    sensor_min_m = Value(0.0)
    sensor_max_m = Value(10.0)
    empty_level = Value(0.0)
    full_level = Value(10.0)
    type = Value(SensorType.SUBMERSIBLE)
    volume_curve = VolumeCurve()
    hide_volume = Value(False)
    max_volume = Value(1000.0)
    depth_units = Value(DepthUnits.METRE)

    # Borrow the real conversion helpers rather than mirroring them, so this
    # stub can't drift from the config the app actually runs against.
    depth_unit = CommonAnalogLevelSensorConfig.depth_unit
    depth_unit_factor = CommonAnalogLevelSensorConfig.depth_unit_factor
    depth_unit_precision = CommonAnalogLevelSensorConfig.depth_unit_precision
    metres_to_depth_units = CommonAnalogLevelSensorConfig.metres_to_depth_units


class FakeTag:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    async def set(self, value):
        self.value = value


class FakeTags:
    def __init__(self):
        self.level_filled_percentage = FakeTag()
        self.level_reading = FakeTag()
        self.level_reading_display = FakeTag()
        self.raw_level_reading = FakeTag()
        self.level_volume = FakeTag()
        self.sensor_fault = FakeTag()
        self.sensor_fault_hidden = FakeTag(True)
        self.sensor_fault_message = FakeTag()


class FakeApp(CommonAnalogLevelSensorApplication):
    def __init__(self):
        # A config per app, so a test that swaps a value can't leak into the next.
        self.config = FakeConfig()
        self.tags = FakeTags()


@pytest.mark.asyncio
async def test_handle_update_writes_level_tags():
    app = FakeApp()

    await app.handle_update(12.0)

    assert app.tags.raw_level_reading.value == 12.0
    assert app.tags.level_reading.value == 5.0
    assert app.tags.level_filled_percentage.value == 50.0
    assert app.tags.level_volume.value == 500.0
    # depth_units defaults to metres, so the display copy matches
    assert app.tags.level_reading_display.value == 5.0


# -- under range: the clamp band and the sensor fault --------------------------------

UNDER_RANGE = 0.5  # a dead loop: below the 1 mA fault limit of a 4-20 mA input
LEVEL_TAGS = (
    "level_filled_percentage",
    "level_reading",
    "level_reading_display",
    "level_volume",
)


async def feed(app, *readings):
    for reading in readings:
        await app.handle_update(reading)


def level_values(app):
    return [getattr(app.tags, name).value for name in LEVEL_TAGS]


def test_the_debounce_is_three_samples():
    assert FAULT_DEBOUNCE_SAMPLES == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", [3.9, 3.8, 3.95])
async def test_clamp_band_reads_as_empty_not_a_fault(reading):
    """Up to 0.2 below the minimum input is a healthy sensor at its zero (an
    empty tank): 0 %, published, no fault, and the raw value as read."""
    app = FakeApp()

    await feed(app, *[reading] * 5)

    assert app.tags.sensor_fault.value is None
    assert app.tags.level_filled_percentage.value == 0.0
    assert app.tags.level_reading.value == 0.0
    assert app.tags.level_volume.value == 0.0
    assert app.tags.raw_level_reading.value == reading
    assert app.tags.sensor_fault_hidden.value is True


@pytest.mark.asyncio
async def test_steady_under_range_faults_after_three_samples(caplog):
    app = FakeApp()

    with caplog.at_level(logging.INFO):
        await feed(app, UNDER_RANGE, UNDER_RANGE)
        assert app.tags.sensor_fault.value is None
        await app.handle_update(UNDER_RANGE)
        await feed(app, *[UNDER_RANGE] * 10)

    assert app.tags.sensor_fault.value == "under_range"
    assert level_values(app) == [None] * 4
    assert app.tags.raw_level_reading.value == UNDER_RANGE
    assert app.tags.sensor_fault_hidden.value is False
    assert app.tags.sensor_fault_message.value == (
        "Sensor signal below range (0.50 mA) — check the sensor and its wiring"
    )
    # One warning on entering, not one a second.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "0.5" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_a_fault_after_good_readings_clears_the_stale_level():
    """The values from before the failure must not be left looking live."""
    app = FakeApp()
    await app.handle_update(12.0)
    assert level_values(app) == [50.0, 5.0, 5.0, 500.0]

    await feed(app, UNDER_RANGE, UNDER_RANGE, UNDER_RANGE)

    assert level_values(app) == [None] * 4
    assert app.tags.sensor_fault.value == "under_range"


@pytest.mark.asyncio
async def test_a_single_spike_does_not_fault_and_holds_the_level():
    app = FakeApp()
    await app.handle_update(12.0)

    await feed(app, 0.5, 12.0, 0.5, 0.5, 12.0, 0.5)

    assert app.tags.sensor_fault.value is None
    assert level_values(app) == [50.0, 5.0, 5.0, 500.0]
    # The spike itself still reaches the raw tag.
    assert app.tags.raw_level_reading.value == 0.5


@pytest.mark.asyncio
async def test_recovery_needs_three_good_samples(caplog):
    app = FakeApp()
    await feed(app, UNDER_RANGE, UNDER_RANGE, UNDER_RANGE)

    with caplog.at_level(logging.INFO):
        # A good run broken by another fault sample starts the count again.
        await feed(app, 12.0, 12.0, UNDER_RANGE, 12.0, 12.0)
        assert app.tags.sensor_fault.value == "under_range"
        assert level_values(app) == [None] * 4
        assert app.tags.raw_level_reading.value == 12.0

        await app.handle_update(12.0)

    assert app.tags.sensor_fault.value is None
    assert app.tags.sensor_fault_hidden.value is True
    assert level_values(app) == [50.0, 5.0, 5.0, 500.0]
    recovered = [r for r in caplog.records if "back in range" in r.getMessage()]
    assert len(recovered) == 1
    assert recovered[0].levelno == logging.INFO


@pytest.mark.asyncio
async def test_clamp_band_samples_count_towards_recovery():
    app = FakeApp()
    await feed(app, UNDER_RANGE, UNDER_RANGE, UNDER_RANGE)

    await feed(app, 3.9, 3.9, 3.9)

    assert app.tags.sensor_fault.value is None
    assert app.tags.level_filled_percentage.value == 0.0


@pytest.mark.asyncio
async def test_a_fault_published_before_a_restart_still_needs_good_samples():
    app = FakeApp()
    app.tags.sensor_fault.value = "under_range"

    await feed(app, 12.0, 12.0)
    assert app.tags.sensor_fault.value == "under_range"
    assert app.tags.level_reading.value is None

    await app.handle_update(12.0)
    assert app.tags.sensor_fault.value is None
    assert app.tags.level_reading.value == 5.0


@pytest.mark.asyncio
async def test_no_reading_publishes_nothing():
    app = FakeApp()

    assert await app.handle_update(None) is None

    assert app.tags.raw_level_reading.value is None
    assert app.tags.sensor_fault.value is None


@pytest.mark.asyncio
async def test_fault_threshold_follows_the_configured_minimum():
    app = FakeApp()
    app.config.sensor_min_mA = Value(0.0)

    # A 0-20 mA input can never read 0.2 below its zero, so 0 is empty.
    await feed(app, 0.0, 0.0, 0.0)

    assert app.tags.sensor_fault.value is None
    assert app.tags.level_filled_percentage.value == 0.0


@pytest.mark.asyncio
async def test_volume_is_published_even_when_hidden():
    """Volume must reach the wire regardless of hide_volume so peer apps (the
    HMI) can display it. hide_volume only affects this app's own gauge."""
    app = FakeApp()
    app.config.hide_volume = Value(True)

    await app.handle_update(12.0)

    assert app.tags.level_volume.value == 500.0


@pytest.mark.asyncio
async def test_display_tag_is_converted_while_level_reading_stays_metres():
    """level_reading is canonical metres for peer apps; the display copy carries
    the configured depth unit for this app's own gauge."""
    app = FakeApp()
    app.config.depth_units = Value(DepthUnits.MILLIMETRE)

    await app.handle_update(12.0)

    assert app.tags.level_reading.value == 5.0
    assert app.tags.level_reading_display.value == 5000.0


class DenseConfig(FakeConfig):
    fluid_density = Value(1250.0)


class DenseRadarConfig(DenseConfig):
    type = Value(SensorType.RADAR_INV)


def test_level_defaults_to_water_when_density_missing():
    # FakeConfig has no fluid_density, like a deployment config predating it.
    assert FakeApp()._level_reading(12.0) == 5.0


def test_submersible_level_scaled_by_fluid_density():
    app = FakeApp()
    app.config = DenseConfig()

    # 5 m of water column / SG 1.25 = 4 m of fluid.
    assert app._level_reading(12.0) == pytest.approx(4.0)


def test_radar_level_ignores_fluid_density():
    app = FakeApp()
    app.config = DenseRadarConfig()

    assert app._level_reading(12.0) == 5.0


class DenseOffsetConfig(DenseConfig):
    sensor_min_m = Value(0.015)
    sensor_max_m = Value(10.015)


def test_density_scaling_leaves_mounting_offset_alone():
    app = FakeApp()
    app.config = DenseOffsetConfig()

    # 15 mm offset + (5 m water column / SG 1.25).
    assert app._level_reading(12.0) == pytest.approx(4.015)


class CurvePoint:
    def __init__(self, level, volume):
        self.level = Value(level)
        self.volume = Value(volume)


CURVE = [CurvePoint(0.0, 0.0), CurvePoint(0.1, 77.66), CurvePoint(0.83, 1019.76)]


def test_volume_interpolates_within_the_curve():
    vol = CommonAnalogLevelSensorApplication._get_volume(0.05, CURVE)
    assert vol == pytest.approx(38.83)


def test_volume_extrapolates_past_the_curve_ends():
    above = CommonAnalogLevelSensorApplication._get_volume(0.9, CURVE)
    below = CommonAnalogLevelSensorApplication._get_volume(-0.1, CURVE)
    assert above > 1019.76
    assert below < 0.0


def test_volume_curve_order_does_not_matter():
    shuffled = [CURVE[2], CURVE[0], CURVE[1]]
    assert CommonAnalogLevelSensorApplication._get_volume(
        0.05, shuffled
    ) == pytest.approx(38.83)

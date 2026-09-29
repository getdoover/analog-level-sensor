import pytest

from common.common_app import CommonAnalogLevelSensorApplication
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
    def __init__(self):
        self.value = None

    async def set(self, value):
        self.value = value


class FakeTags:
    def __init__(self):
        self.level_filled_percentage = FakeTag()
        self.level_reading = FakeTag()
        self.level_reading_display = FakeTag()
        self.raw_level_reading = FakeTag()
        self.level_volume = FakeTag()


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


@pytest.mark.asyncio
async def test_handle_update_ignores_below_sensor_minimum():
    app = FakeApp()

    await app.handle_update(3.9)

    assert app.tags.raw_level_reading.value is None


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

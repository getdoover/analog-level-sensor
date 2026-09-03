from types import SimpleNamespace

import pytest

from analog_level_sensor_processor.application import (
    AnalogLevelSensorProcessorApplication,
)
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
    input_message_path = Value("$on_dm_event.analog_input_v")
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


def make_event(channel: str, data: dict):
    return SimpleNamespace(
        channel=SimpleNamespace(name=channel),
        message=SimpleNamespace(data=data),
    )


def make_app():
    app = object.__new__(AnalogLevelSensorProcessorApplication)
    app.config = FakeConfig()
    app.tags = FakeTags()
    return app


def test_parse_input_message_path():
    assert AnalogLevelSensorProcessorApplication._parse_input_message_path(
        "$on_dm_event.analog_input_v"
    ) == ("on_dm_event", ["analog_input_v"])
    assert AnalogLevelSensorProcessorApplication._parse_input_message_path(
        "$on_dm_event:payload.analog_input_v"
    ) == ("on_dm_event", ["payload", "analog_input_v"])


@pytest.mark.asyncio
async def test_message_input_path_updates_level_tags():
    app = make_app()
    await app.setup()

    await app.on_message_create(make_event("on_dm_event", {"analog_input_v": "12.0"}))

    assert app.tags.raw_level_reading.value == 12.0
    assert app.tags.level_reading.value == 5.0
    assert app.tags.level_filled_percentage.value == 50.0
    assert app.tags.level_volume.value == 500.0


@pytest.mark.asyncio
async def test_message_input_ignores_other_channels():
    app = make_app()
    await app.setup()

    await app.on_message_create(make_event("other", {"analog_input_v": "12.0"}))

    assert app.tags.raw_level_reading.value is None

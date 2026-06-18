import pytest

from common.common_app import CommonAnalogLevelSensorApplication
from common.common_config import SensorType


class Value:
    def __init__(self, value):
        self.value = value


class VolumeCurve:
    elements = []


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


class FakeTag:
    def __init__(self):
        self.value = None

    async def set(self, value):
        self.value = value


class FakeTags:
    def __init__(self):
        self.level_filled_percentage = FakeTag()
        self.level_reading = FakeTag()
        self.raw_level_reading = FakeTag()
        self.level_volume = FakeTag()


class FakeApp(CommonAnalogLevelSensorApplication):
    config = FakeConfig()

    def __init__(self):
        self.tags = FakeTags()


@pytest.mark.asyncio
async def test_handle_update_writes_level_tags():
    app = FakeApp()

    await app.handle_update(12.0)

    assert app.tags.raw_level_reading.value == 12.0
    assert app.tags.level_reading.value == 5.0
    assert app.tags.level_filled_percentage.value == 50.0
    assert app.tags.level_volume.value == 500.0


@pytest.mark.asyncio
async def test_handle_update_ignores_below_sensor_minimum():
    app = FakeApp()

    await app.handle_update(3.9)

    assert app.tags.raw_level_reading.value is None

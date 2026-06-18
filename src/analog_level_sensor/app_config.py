from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition

from common.common_config import (
    CommonAnalogLevelSensorConfig,
    SensorType,
    VolumeCurvePoint,
)


class AnalogLevelSensorDeviceConfig(CommonAnalogLevelSensorConfig):
    ai_pin = config.Integer(
        "AI Pin",
        description="Analog input pin number",
        position=0,
    )
    power_pin = config.Integer(
        "Power Pin",
        description="Digital output pin to power the sensor",
        default=None,
        position=8,
    )
    polling_frequency = config.Number(
        "Polling Frequency",
        description="How often to poll the sensor (Hz)",
        default=1.0,
        position=9,
    )

    position = ApplicationPosition(position=16)


AnalogLevelSensorConfig = AnalogLevelSensorDeviceConfig


def export():
    AnalogLevelSensorDeviceConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor",
    )

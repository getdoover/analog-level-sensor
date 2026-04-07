from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition


class SensorType:
    SUBMERSIBLE = "Submersible"
    RADAR = "Radar"
    RADAR_INV = "Radar Inverted"


class VolumeCurvePoint(config.Object):
    level = config.Number("Level", description="Level / depth in metres")
    volume = config.Number("Volume", description="Volume in litres")


class AnalogLevelSensorConfig(config.Schema):
    ai_pin = config.Integer("AI Pin", description="Analog input pin number")
    sensor_max_m = config.Number(
        "Sensor Maximum Metres", description="Maximum sensor depth (m)"
    )
    full_level = config.Number(
        "Full Level", description="Level reading when full (m)"
    )

    sensor_min_mA = config.Number(
        "Sensor Minimum mA", description="Minimum sensor output (mA)", default=4.0
    )
    sensor_max_mA = config.Number(
        "Sensor Maximum mA", description="Maximum sensor output (mA)", default=20.0
    )
    sensor_min_m = config.Number(
        "Sensor Minimum Metres", description="Minimum sensor depth (m)", default=0.0
    )
    empty_level = config.Number(
        "Empty Level", description="Level reading when empty (m)", default=0.0
    )

    power_pin = config.Integer(
        "Power Pin", description="Digital output pin to power the sensor", default=None
    )
    type = config.Enum(
        "Sensor Type",
        description="Type of sensor. Radar inverted reads like a submersible sensor.",
        choices=[SensorType.SUBMERSIBLE, SensorType.RADAR, SensorType.RADAR_INV],
        default=SensorType.SUBMERSIBLE,
    )
    volume_curve = config.Array(
        "Volume Curve",
        element=VolumeCurvePoint("Volume Curve Point"),
    )

    position = ApplicationPosition()


def export():
    AnalogLevelSensorConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor",
    )

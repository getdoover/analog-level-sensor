from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition


class SensorType:
    SUBMERSIBLE = "Submersible"
    RADAR = "Radar"
    RADAR_INV = "Radar Inverted"


class VolumeCurvePoint(config.Object):
    level = config.Number("Level", description="Level / depth in metres")
    volume = config.Number("Volume", description="Volume in configured volume units")


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
    polling_frequency = config.Number(
        "Polling Frequency",
        description="How often to poll the sensor (Hz)",
        default=1.0,
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

    hide_volume = config.Boolean(
        "Hide Volume",
        description="Whether to hide the tank volume in the UI",
        default=True,
    )
    max_volume = config.Number(
        "Max Volume",
        description="Maximum tank volume in the configured volume units, used when no volume curve is configured",
        default=100000.0,
    )
    volume_units = config.String(
        "Volume Units",
        description="Units to display the volume reading in (e.g. L, kL, gal)",
        default="L",
    )
    volume_precision = config.Integer(
        "Volume Decimal Precision",
        description="Number of decimal places to show for the volume reading",
        default=0,
    )

    position = ApplicationPosition()


def export():
    AnalogLevelSensorConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor",
    )

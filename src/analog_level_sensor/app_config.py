from enum import Enum
from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition

from common.common_config import (
    CommonAnalogLevelSensorConfig,
    SensorType,
    VolumeCurvePoint,
)

from .alarm import AlarmType


class AlarmSource(Enum):
    level = "Level Reading"
    percentage = "Filled Percentage"
    volume = "Volume"


def _config_value(element, fallback):
    """Read a config element an older deployment config may not carry.

    An app install created before the alarm existed has no ``alarm`` block, so
    pydoover leaves its sub-elements unset and ``.value`` raises. Treat that as
    "the operator never configured an alarm" rather than an error.
    """
    try:
        value = element.value
    except ValueError:
        return fallback
    return fallback if value is None else value


class AlarmConfig(config.Object):
    alarm_enabled = config.Boolean(
        "Alarm Enabled",
        default=False,
        description="Send a notification when the reading crosses the alarm point",
    )
    alarm_type = config.Enum(
        "Alarm Type",
        choices=AlarmType,
        default=AlarmType.greater_than,
        description=(
            "Greater Than and Less Than alarm on a single point. Allowed Range "
            "alarms when the reading falls outside a low/high band."
        ),
    )
    alarm_source = config.Enum(
        "Alarm Source",
        choices=AlarmSource,
        default=AlarmSource.level,
        description="Which reading the alarm tracks. Sets the slider's bounds and units.",
    )
    slider_min = config.Number(
        "Alarm Slider Minimum",
        default=None,
        description="Lower bound of the alarm point slider. Defaults to the alarm source's minimum.",
    )
    slider_max = config.Number(
        "Alarm Slider Maximum",
        default=None,
        description="Upper bound of the alarm point slider. Defaults to the alarm source's maximum.",
    )
    grace_period = config.Number(
        "Alarm Grace Period (s)",
        default=30.0,
        description="How long the reading must stay out of bounds before notifying",
        minimum=0.0,
    )
    renotify_interval = config.Number(
        "Alarm Re-notify Interval (s)",
        default=900.0,
        description="How often to re-notify while the alarm remains active",
        minimum=0.0,
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

    # Optional, so injecting a deployment config written before the alarm
    # existed cannot raise on a missing required element.
    alarm = AlarmConfig(
        "Alarm",
        default=None,
        description="Alarm configuration",
        position=17,
    )

    position = ApplicationPosition(position=16)

    @property
    def alarm_enabled(self) -> bool:
        return bool(_config_value(self.alarm.alarm_enabled, False))

    @property
    def alarm_type(self) -> AlarmType:
        # config.Enum yields a member when declared from an EnumType, but a raw
        # string when the value arrives from an injected deployment config.
        value = _config_value(self.alarm.alarm_type, AlarmType.greater_than)
        return value if isinstance(value, AlarmType) else AlarmType(value)

    @property
    def alarm_source(self) -> AlarmSource:
        value = _config_value(self.alarm.alarm_source, AlarmSource.level)
        return value if isinstance(value, AlarmSource) else AlarmSource(value)

    @property
    def alarm_grace_period(self) -> float:
        return float(_config_value(self.alarm.grace_period, 30.0))

    @property
    def alarm_renotify_interval(self) -> float:
        return float(_config_value(self.alarm.renotify_interval, 900.0))

    @property
    def alarm_units(self) -> str:
        source = self.alarm_source
        if source is AlarmSource.percentage:
            return "%"
        if source is AlarmSource.volume:
            return self.volume_units.value
        return "m"

    @property
    def alarm_slider_min(self) -> float:
        value = _config_value(self.alarm.slider_min, None)
        return self._alarm_source_range()[0] if value is None else float(value)

    @property
    def alarm_slider_max(self) -> float:
        value = _config_value(self.alarm.slider_max, None)
        return self._alarm_source_range()[1] if value is None else float(value)

    def _alarm_source_range(self) -> tuple[float, float]:
        """The source's own span, used when the slider bounds are left unset."""
        source = self.alarm_source
        if source is AlarmSource.percentage:
            return 0.0, 100.0
        if source is AlarmSource.volume:
            return 0.0, self._full_scale_volume()
        return float(self.sensor_min_m.value), float(self.sensor_max_m.value)

    def _full_scale_volume(self) -> float:
        curve = self.volume_curve.elements
        if len(curve) >= 2:
            return max(float(point.volume.value) for point in curve)
        return float(self.max_volume.value)


AnalogLevelSensorConfig = AnalogLevelSensorDeviceConfig


def export():
    AnalogLevelSensorDeviceConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor",
    )

from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition

from common.common_config import (
    CommonAnalogLevelSensorConfig,
    ReadingType,
)

from .alarm import AlarmType


# The alarm now tracks the shared Reading Type. AlarmSource is retained as an
# alias so the deprecated fallback field and existing imports keep working.
AlarmSource = ReadingType


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

    # The alarm fields sit at the top level rather than under a nested object.
    # pydoover types a non-required element as ``[<type>, "null"]``, and the
    # config form only recognises objects declared as a bare ``"object"``, so a
    # nested optional block never renders. Flat, optional scalars also let an
    # older deployment config fall back to these defaults instead of raising.
    #
    # The published keys come from the display names, not these attributes:
    # "Alarm Slider Minimum" -> alarm_slider_minimum, "Alarm Grace Period (s)"
    # -> alarm_grace_period_s. The leading underscore keeps each element from
    # colliding with the derived property of the same name below.
    _alarm_enabled = config.Boolean(
        "Alarm Enabled",
        default=False,
        description="Send a notification when the reading crosses the alarm point",
        position=17,
    )
    _alarm_type = config.Enum(
        "Alarm Type",
        choices=AlarmType,
        default=AlarmType.greater_than,
        description=(
            "Greater Than and Less Than alarm on a single point. Allowed Range "
            "alarms when the reading falls outside a low/high band."
        ),
        position=18,
    )
    # Superseded by the shared Reading Type element. Kept (hidden) so that a
    # legacy stored config carrying an explicit null Reading Type keeps alarming
    # on its historic source; any resolved Reading Type overrides it. See the
    # ``alarm_source`` property.
    _alarm_source = config.Enum(
        "Alarm Source",
        choices=AlarmSource,
        default=AlarmSource.level,
        description="Deprecated: superseded by Reading Type.",
        hidden=True,
        deprecated=True,
        position=19,
    )
    _alarm_slider_min = config.Number(
        "Alarm Slider Minimum",
        default=None,
        description="Lower bound of the alarm point slider. Defaults to the alarm source's minimum.",
        position=20,
    )
    _alarm_slider_max = config.Number(
        "Alarm Slider Maximum",
        default=None,
        description="Upper bound of the alarm point slider. Defaults to the alarm source's maximum.",
        position=21,
    )
    _alarm_grace_period = config.Number(
        "Alarm Grace Period (s)",
        default=30.0,
        description="How long the reading must stay out of bounds before notifying",
        minimum=0.0,
        position=22,
    )
    _alarm_renotify_interval = config.Number(
        "Alarm Re-notify Interval (s)",
        default=900.0,
        description="How often to re-notify while the alarm remains active",
        minimum=0.0,
        position=23,
    )

    position = ApplicationPosition(position=16)

    @property
    def alarm_enabled(self) -> bool:
        return bool(self._alarm_enabled.value)

    @property
    def alarm_type(self) -> AlarmType:
        # config.Enum yields a member when declared from an EnumType, but a raw
        # string when the value arrives from an injected deployment config.
        value = self._alarm_type.value
        return value if isinstance(value, AlarmType) else AlarmType(value)

    @property
    def alarm_source(self) -> ReadingType:
        # A resolved Reading Type drives the alarm; a Reading Type that resolves
        # to None (an explicit null in a legacy stored config) falls back to the
        # deprecated Alarm Source so pre-existing alarms are unchanged. An
        # omitted key resolves to the default and does not reach this fallback.
        reading_type = self.reading_type
        if reading_type is not None:
            return reading_type
        value = self._alarm_source.value
        return value if isinstance(value, ReadingType) else ReadingType(value)

    @property
    def alarm_grace_period(self) -> float:
        return float(self._alarm_grace_period.value)

    @property
    def alarm_renotify_interval(self) -> float:
        return float(self._alarm_renotify_interval.value)

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
        value = self._alarm_slider_min.value
        return self._alarm_source_range()[0] if value is None else float(value)

    @property
    def alarm_slider_max(self) -> float:
        value = self._alarm_slider_max.value
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

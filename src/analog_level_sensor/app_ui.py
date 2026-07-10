from pydoover import ui

from common.common_ui import CommonAnalogLevelSensorUI

from .alarm import AlarmType


class AnalogLevelSensorDeviceUI(CommonAnalogLevelSensorUI):
    # A single slider reports a number and a dual slider reports [low, high], so
    # each mode gets its own element. One element toggling dual_slider would
    # leave behind a stored value of the wrong shape on every mode change.
    alarm_point = ui.Slider(
        "Alarm Point",
        name="alarm_point",
        dual_slider=False,
        inverted=False,
        hidden=True,
    )
    alarm_range = ui.Slider(
        "Allowed Range",
        name="alarm_range",
        dual_slider=True,
        inverted=False,
        hidden=True,
    )

    async def setup(self):
        await super().setup()
        self._setup_alarm()

    def _setup_alarm(self):
        alarm_type = self.config.alarm_type
        enabled = self.config.alarm_enabled
        is_range = alarm_type is AlarmType.allowed_range

        for slider in (self.alarm_point, self.alarm_range):
            slider.min_val = self.config.alarm_slider_min
            slider.max_val = self.config.alarm_slider_max
            slider.units = self.config.alarm_units

        self.alarm_point.hidden = not enabled or is_range
        self.alarm_range.hidden = not enabled or not is_range

        if alarm_type is AlarmType.greater_than:
            self.alarm_point.display_name = "High Alarm Point"
        elif alarm_type is AlarmType.less_than:
            self.alarm_point.display_name = "Low Alarm Point"


AnalogLevelSensorUI = AnalogLevelSensorDeviceUI


def export():
    from pathlib import Path

    AnalogLevelSensorDeviceUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )

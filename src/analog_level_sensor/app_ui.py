from common.common_ui import CommonAnalogLevelSensorUI


class AnalogLevelSensorDeviceUI(CommonAnalogLevelSensorUI):
    pass


AnalogLevelSensorUI = AnalogLevelSensorDeviceUI


def export():
    from pathlib import Path

    AnalogLevelSensorDeviceUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )

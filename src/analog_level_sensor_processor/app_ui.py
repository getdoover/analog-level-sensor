from common.common_ui import CommonAnalogLevelSensorUI


class AnalogLevelSensorProcessorUI(CommonAnalogLevelSensorUI):
    pass


def export():
    from pathlib import Path

    AnalogLevelSensorProcessorUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json",
        "analog_level_sensor_processor",
    )

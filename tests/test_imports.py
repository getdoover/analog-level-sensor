"""
Basic tests for the application.

This ensures all modules are importable and that the config is valid.
"""


def test_import_app():
    from analog_level_sensor.application import AnalogLevelSensorApplication

    assert AnalogLevelSensorApplication


def test_config():
    from analog_level_sensor.app_config import AnalogLevelSensorConfig

    assert isinstance(AnalogLevelSensorConfig.to_schema(), dict)


def test_ui():
    from analog_level_sensor.app_ui import AnalogLevelSensorUI

    assert AnalogLevelSensorUI


def test_tags():
    from analog_level_sensor.app_tags import AnalogLevelSensorTags

    assert AnalogLevelSensorTags

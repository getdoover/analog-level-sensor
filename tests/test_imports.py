"""
Basic tests for an application.

This ensures all modules are importable and that the config is valid.
"""

def test_import_app():
    from analog_level_sensor.application import AnalogLevelSensorApplication
    assert AnalogLevelSensorApplication

def test_config():
    from analog_level_sensor.app_config import AnalogLevelSensorConfig

    config = AnalogLevelSensorConfig()
    assert isinstance(config.to_dict(), dict)

def test_ui():
    from analog_level_sensor.app_ui import AnalogLevelSensorUI
    assert AnalogLevelSensorUI

def test_state():
    from analog_level_sensor.app_state import AnalogLevelSensorState
    assert AnalogLevelSensorState
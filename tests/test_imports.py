"""
Basic tests for the application.

This ensures all modules are importable and that the config is valid.
"""


def test_import_app():
    from analog_level_sensor.application import (
        AnalogLevelSensorApplication,
        AnalogLevelSensorDeviceApplication,
    )

    assert AnalogLevelSensorApplication
    assert AnalogLevelSensorDeviceApplication


def test_import_processor_app():
    from analog_level_sensor_processor import handler
    from analog_level_sensor_processor.application import (
        AnalogLevelSensorProcessorApplication,
    )

    assert AnalogLevelSensorProcessorApplication
    assert handler


def test_config():
    from analog_level_sensor.app_config import (
        AnalogLevelSensorConfig,
        AnalogLevelSensorDeviceConfig,
    )
    from analog_level_sensor_processor.app_config import (
        AnalogLevelSensorProcessorConfig,
    )
    from common.common_config import CommonAnalogLevelSensorConfig

    assert isinstance(AnalogLevelSensorConfig.to_schema(), dict)
    assert isinstance(AnalogLevelSensorDeviceConfig.to_schema(), dict)
    assert isinstance(AnalogLevelSensorProcessorConfig.to_schema(), dict)
    assert isinstance(CommonAnalogLevelSensorConfig.to_schema(), dict)


def test_input_config_keeps_legacy_keys_with_generic_labels():
    from analog_level_sensor.app_config import AnalogLevelSensorDeviceConfig

    schema = AnalogLevelSensorDeviceConfig.to_schema()
    props = schema["properties"]

    assert "input_units" in props
    assert props["sensor_minimum_ma"]["title"] == "Sensor Minimum Input"
    assert props["sensor_maximum_ma"]["title"] == "Sensor Maximum Input"


def test_ui():
    from analog_level_sensor.app_ui import AnalogLevelSensorUI
    from analog_level_sensor_processor.app_ui import AnalogLevelSensorProcessorUI
    from common.common_ui import CommonAnalogLevelSensorUI

    assert AnalogLevelSensorUI
    assert AnalogLevelSensorProcessorUI
    assert CommonAnalogLevelSensorUI


def test_tags():
    from analog_level_sensor.app_tags import AnalogLevelSensorTags
    from analog_level_sensor_processor.app_tags import AnalogLevelSensorProcessorTags
    from common.common_tags import CommonAnalogLevelSensorTags

    assert AnalogLevelSensorTags
    assert AnalogLevelSensorProcessorTags
    assert CommonAnalogLevelSensorTags

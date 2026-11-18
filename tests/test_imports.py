"""
Basic tests for the processor.

This ensures all modules are importable and that the config is valid. The
device app is Rust, in device/, with its own tests.
"""


def test_import_processor_app():
    from analog_level_sensor_processor import handler
    from analog_level_sensor_processor.application import (
        AnalogLevelSensorProcessorApplication,
    )

    assert AnalogLevelSensorProcessorApplication
    assert handler


def test_config():
    from analog_level_sensor_processor.app_config import (
        AnalogLevelSensorProcessorConfig,
    )
    from common.common_config import CommonAnalogLevelSensorConfig

    assert isinstance(AnalogLevelSensorProcessorConfig.to_schema(), dict)
    assert isinstance(CommonAnalogLevelSensorConfig.to_schema(), dict)


def test_input_config_keeps_legacy_keys_with_generic_labels():
    from analog_level_sensor_processor.app_config import (
        AnalogLevelSensorProcessorConfig,
    )

    schema = AnalogLevelSensorProcessorConfig.to_schema()
    props = schema["properties"]

    assert "input_units" in props
    assert props["sensor_minimum_ma"]["title"] == "Sensor Minimum Input"
    assert props["sensor_maximum_ma"]["title"] == "Sensor Maximum Input"


def test_ui():
    from analog_level_sensor_processor.app_ui import AnalogLevelSensorProcessorUI
    from common.common_ui import CommonAnalogLevelSensorUI

    assert AnalogLevelSensorProcessorUI
    assert CommonAnalogLevelSensorUI


def test_tags():
    from analog_level_sensor_processor.app_tags import AnalogLevelSensorProcessorTags
    from common.common_tags import CommonAnalogLevelSensorTags

    assert AnalogLevelSensorProcessorTags
    assert CommonAnalogLevelSensorTags

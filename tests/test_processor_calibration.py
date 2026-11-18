"""Operator Sensor Calibration is the device app's alone (device/src/calibration.rs)."""

from analog_level_sensor_processor.app_config import AnalogLevelSensorProcessorConfig
from analog_level_sensor_processor.app_tags import AnalogLevelSensorProcessorTags
from analog_level_sensor_processor.app_ui import AnalogLevelSensorProcessorUI


def test_processor_keeps_the_config_conversion():
    """The processor variant has no operator calibration: no config field, no
    readback tags, no submodule, and the shared conversion on the config."""
    assert not hasattr(
        AnalogLevelSensorProcessorConfig, "_operator_calibration_enabled"
    )
    assert not hasattr(AnalogLevelSensorProcessorTags, "zero_m")
    assert not hasattr(AnalogLevelSensorProcessorUI, "sensor_calibration")

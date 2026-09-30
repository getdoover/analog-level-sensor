from pydoover.tags import Tag

from common.common_tags import CommonAnalogLevelSensorTags


class AnalogLevelSensorDeviceTags(CommonAnalogLevelSensorTags):
    # Operator Sensor Calibration readbacks (calibration.py): the values the
    # 4-20 mA conversion is using now. Only published while the feature is
    # enabled, so they stay None on a deployment that has it off; the HMI
    # locks its Sensor cells unless operator_calibration is true.
    zero_m = Tag("number", default=None, live=True)
    span_m = Tag("number", default=None, live=True)
    fluid_density = Tag("number", default=None, live=True)
    operator_calibration = Tag("boolean", default=None, live=True)


AnalogLevelSensorTags = AnalogLevelSensorDeviceTags

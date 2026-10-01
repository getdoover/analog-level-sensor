from pydoover.tags import Tag, Tags

# sensor_fault values. None means the input is trusted; more codes may follow.
SENSOR_FAULT_UNDER_RANGE = "under_range"

SENSOR_FAULT_DEFAULT_MESSAGE = (
    "Sensor signal below range — check the sensor and its wiring"
)


class CommonAnalogLevelSensorTags(Tags):
    level_filled_percentage = Tag("number", default=None, live=True)
    level_reading = Tag("number", default=None, live=True)
    # level_reading converted to the configured depth units, for this app's own
    # gauge; peers should keep reading level_reading (metres).
    level_reading_display = Tag("number", default=None, live=True)
    raw_level_reading = Tag("number", default=None)
    level_volume = Tag("number", default=None)
    # Peer contract (sia-local-control HMI, sia-injection-controller): a code
    # while the input is in fault (SENSOR_FAULT_UNDER_RANGE), None otherwise.
    # While it is set the level, percentage, display and volume tags are None.
    sensor_fault = Tag("string", default=None, live=True)
    # Drive this app's own warning indicator (common_ui). The UI schema is
    # published once, so its visibility and text are bound to tags.
    sensor_fault_hidden = Tag("boolean", default=True)
    sensor_fault_message = Tag("string", default=SENSOR_FAULT_DEFAULT_MESSAGE)


CommonTags = CommonAnalogLevelSensorTags

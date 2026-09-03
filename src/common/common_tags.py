from pydoover.tags import Tag, Tags


class CommonAnalogLevelSensorTags(Tags):
    level_filled_percentage = Tag("number", default=None, live=True)
    level_reading = Tag("number", default=None, live=True)
    # level_reading converted to the configured depth units, for this app's own
    # gauge; peers should keep reading level_reading (metres).
    level_reading_display = Tag("number", default=None, live=True)
    raw_level_reading = Tag("number", default=None)
    level_volume = Tag("number", default=None)


CommonTags = CommonAnalogLevelSensorTags

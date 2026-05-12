from pydoover.tags import Tag, Tags


class AnalogLevelSensorTags(Tags):
    level_filled_percentage = Tag("number", default=None)
    level_reading = Tag("number", default=None)
    raw_level_reading = Tag("number", default=None)
    level_volume = Tag("number", default=None)

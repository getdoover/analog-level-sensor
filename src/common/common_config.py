from enum import Enum

from pydoover import config


class ReadingType(Enum):
    level = "Level Reading"
    percentage = "Filled Percentage"
    volume = "Volume"


# Published as a list of plain strings (not the EnumType). Defaults to
# "Filled Percentage": a null default fails schema validation against the
# enum (null is not a member), which invalidates any config form containing
# this app — e.g. a solution's Edit Config dialog — and disables its Save.
# Installs that predate this element and read back as None still fall back
# to the legacy multi-reading layout and the deprecated Alarm Source.
_READING_TYPE_CHOICES = [rt.value for rt in ReadingType]


class SensorType:
    SUBMERSIBLE = "Submersible"
    RADAR = "Radar"
    RADAR_INV = "Radar Inverted"


class InputUnits:
    MILLIAMPS = "mA"
    VOLTS = "V"
    RAW = "raw"


class DepthUnits:
    # The value doubles as the display label; a consumer converts the metres the
    # sensor works in into the chosen unit.
    METRE = "m"
    CENTIMETRE = "cm"
    MILLIMETRE = "mm"
    INCH = "in"
    FOOT = "ft"


class VolumeCurvePoint(config.Object):
    level = config.Number("Level", description="Level / depth in metres")
    volume = config.Number("Volume", description="Volume in configured volume units")


class CommonAnalogLevelSensorConfig(config.Schema):
    # Underscore-prefixed so the derived ``reading_type`` property below can take
    # the clean attribute name; the published key is set explicitly. Sits at the
    # top of the form as the headline selector.
    _reading_type = config.Enum(
        "Reading Type",
        name="reading_type",
        choices=_READING_TYPE_CHOICES,
        default=ReadingType.percentage.value,
        description=(
            "Which reading to display and alarm on: the level/depth, the filled "
            "percentage, or the volume. Focuses the UI on that single reading and "
            "points the alarm at it."
        ),
        position=0,
    )

    sensor_max_m = config.Number(
        "Sensor Maximum Metres",
        description="Maximum sensor depth (m)",
        position=1,
    )
    full_level = config.Number(
        "Full Level",
        description="Level reading when full (m)",
        position=2,
    )
    input_units = config.Enum(
        "Input Units",
        description="Units of the raw analog input reading.",
        choices=[InputUnits.MILLIAMPS, InputUnits.VOLTS, InputUnits.RAW],
        default=InputUnits.MILLIAMPS,
        position=3,
    )

    sensor_min_mA = config.Number(
        "Sensor Minimum Input",
        description="Minimum raw sensor input in the configured input units.",
        name="sensor_minimum_ma",
        default=4.0,
        position=4,
    )
    sensor_max_mA = config.Number(
        "Sensor Maximum Input",
        description="Maximum raw sensor input in the configured input units.",
        name="sensor_maximum_ma",
        default=20.0,
        position=5,
    )
    sensor_min_m = config.Number(
        "Sensor Minimum Metres",
        description="Minimum sensor depth (m)",
        default=0.0,
        position=6,
    )
    empty_level = config.Number(
        "Empty Level",
        description="Level reading when empty (m)",
        default=0.0,
        position=7,
    )

    type = config.Enum(
        "Sensor Type",
        description="Type of sensor. Radar inverted reads like a submersible sensor.",
        choices=[SensorType.SUBMERSIBLE, SensorType.RADAR, SensorType.RADAR_INV],
        default=SensorType.SUBMERSIBLE,
        position=10,
    )
    volume_curve = config.Array(
        "Volume Curve",
        element=VolumeCurvePoint("Volume Curve Point"),
        position=11,
    )

    hide_volume = config.Boolean(
        "Hide Volume",
        description="Whether to hide the tank volume in the UI",
        default=True,
        position=12,
    )
    max_volume = config.Number(
        "Max Volume",
        description="Maximum tank volume in the configured volume units, used when no volume curve is configured",
        default=100000.0,
        position=13,
    )
    volume_units = config.String(
        "Volume Units",
        description="Units to display the volume reading in (e.g. L, kL, gal)",
        default="L",
        position=14,
    )
    depth_units = config.Enum(
        "Depth Units",
        choices=[
            DepthUnits.METRE,
            DepthUnits.CENTIMETRE,
            DepthUnits.MILLIMETRE,
            DepthUnits.INCH,
            DepthUnits.FOOT,
        ],
        default=DepthUnits.METRE,
        description=(
            "Units to display the level/depth reading in. The sensor measures in "
            "metres; consumers convert to this unit for display."
        ),
        position=14,
    )
    volume_precision = config.Integer(
        "Volume Decimal Precision",
        description="Number of decimal places to show for the volume reading",
        default=0,
        position=15,
    )

    @property
    def reading_type(self) -> "ReadingType | None":
        """The selected reading type, or None when the stored config carries an
        explicit null (e.g. a legacy install predating this element), in which
        case the legacy multi-reading layout and Alarm Source apply. An omitted
        key resolves to the "Filled Percentage" default, not None."""
        value = self._reading_type.value
        if value is None or value == "":
            return None
        return value if isinstance(value, ReadingType) else ReadingType(value)


CommonConfig = CommonAnalogLevelSensorConfig

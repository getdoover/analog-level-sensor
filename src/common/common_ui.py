from pydoover import ui

from .common_config import ReadingType
from .common_tags import CommonAnalogLevelSensorTags

# Boundary between the "Low" and "Good" colour bands, as a fraction of the
# gauge's full-scale value. Used for both the percentage and volume gauges so
# the two stay visually consistent.
_LOW_BAND = 0.15


class CommonAnalogLevelSensorUI(ui.UI):
    percentage = ui.NumericVariable(
        "Level",
        units="%",
        value=CommonAnalogLevelSensorTags.level_filled_percentage,
        precision=1,
        form=ui.Widget.radial,
        ranges=[
            ui.Range("Low", 0, _LOW_BAND * 100, ui.Colour.blue),
            ui.Range("Good", _LOW_BAND * 100, 100, ui.Colour.green),
        ],
    )

    level_reading = ui.NumericVariable(
        "Level Reading",
        units="m",
        value=CommonAnalogLevelSensorTags.level_reading,
        precision=2,
    )

    volume = ui.NumericVariable(
        "Tank Volume",
        name="volume",  # keep the element key stable; only relabel the display
        units="L",
        value=CommonAnalogLevelSensorTags.level_volume,
        precision=0,
        hidden=True,
    )

    async def setup(self):
        self.volume.units = self.config.volume_units.value

        # A resolved reading type focuses the UI on that single reading; a
        # Reading Type that resolves to None (an explicit null in a legacy
        # stored config) keeps the historic multi-reading layout. An omitted
        # key resolves to the "Filled Percentage" default, not None.
        reading_type = self.config.reading_type
        if reading_type is None:
            self._setup_legacy_layout()
        else:
            self._setup_single_reading(reading_type)

    def _setup_legacy_layout(self):
        # With volume display disabled, keep the default layout: the percentage
        # level is the primary radial gauge, with the level reading below it.
        if self.config.hide_volume.value:
            return

        # Volume display is enabled -> promote volume to the primary radial
        # gauge at the top, and demote the percentage level to a plain reading
        # below it (no radial gauge). Level reading stays as a secondary value.
        max_vol = self._gauge_max_volume()
        self.volume.hidden = False
        self.volume.precision = int(self.config.volume_precision.value)
        self.volume.form = ui.Widget.radial
        self.volume.ranges = [
            ui.Range("Low", 0, _LOW_BAND * max_vol, ui.Colour.blue),
            ui.Range("Good", _LOW_BAND * max_vol, max_vol, ui.Colour.green),
        ]
        self.volume.position = 10

        self.percentage.form = ui.NotSet
        self.percentage.ranges = ui.NotSet
        self.percentage.position = 20

        self.level_reading.position = 30

    def _setup_single_reading(self, reading_type: ReadingType):
        """Show only the selected reading, as the primary radial gauge."""
        self.percentage.hidden = True
        self.level_reading.hidden = True
        self.volume.hidden = True

        if reading_type is ReadingType.percentage:
            # percentage keeps its class-level radial form and 0-100 bands.
            self.percentage.hidden = False
            self.percentage.position = 10
        elif reading_type is ReadingType.volume:
            max_vol = self._gauge_max_volume()
            self.volume.hidden = False
            self.volume.precision = int(self.config.volume_precision.value)
            self.volume.form = ui.Widget.radial
            self.volume.ranges = [
                ui.Range("Low", 0, _LOW_BAND * max_vol, ui.Colour.blue),
                ui.Range("Good", _LOW_BAND * max_vol, max_vol, ui.Colour.green),
            ]
            self.volume.position = 10
        else:  # ReadingType.level
            full = float(self.config.full_level.value)
            self.level_reading.hidden = False
            self.level_reading.form = ui.Widget.radial
            if full > 0:
                self.level_reading.ranges = [
                    ui.Range("Low", 0, _LOW_BAND * full, ui.Colour.blue),
                    ui.Range("Good", _LOW_BAND * full, full, ui.Colour.green),
                ]
            self.level_reading.position = 10

    def _gauge_max_volume(self) -> float:
        """Full-scale value for the volume gauge, in configured volume units."""
        curve = self.config.volume_curve.elements
        if len(curve) >= 2:
            return max(float(p.volume.value) for p in curve)
        return float(self.config.max_volume.value)


CommonUI = CommonAnalogLevelSensorUI

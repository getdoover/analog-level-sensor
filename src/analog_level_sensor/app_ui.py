from pydoover import ui

from .app_tags import AnalogLevelSensorTags


# Boundary between the "Low" and "Good" colour bands, as a fraction of the
# gauge's full-scale value. Used for both the percentage and volume gauges so
# the two stay visually consistent.
_LOW_BAND = 0.15


class AnalogLevelSensorUI(ui.UI):
    percentage = ui.NumericVariable(
        "Level",
        units="%",
        value=AnalogLevelSensorTags.level_filled_percentage,
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
        value=AnalogLevelSensorTags.level_reading,
        precision=2,
    )

    volume = ui.NumericVariable(
        "Volume",
        units="L",
        value=AnalogLevelSensorTags.level_volume,
        precision=0,
        hidden=True,
    )

    async def setup(self):
        # With volume display disabled, keep the default layout: the percentage
        # level is the primary radial gauge, with the level reading below it.
        if self.config.hide_volume.value:
            return

        # Volume display is enabled -> promote volume to the primary radial
        # gauge at the top, and demote the percentage level to a plain reading
        # below it (no radial gauge). Level reading stays as a secondary value.
        max_vol = self._gauge_max_volume()
        self.volume.hidden = False
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

    def _gauge_max_volume(self) -> float:
        """Full-scale value for the volume gauge, in litres.

        Matches the application's volume logic: the top of the volume curve
        when one is configured, otherwise the configured max volume.
        """
        curve = self.config.volume_curve.elements
        if len(curve) >= 2:
            return max(float(p.volume.value) for p in curve)
        return float(self.config.max_volume.value)


def export():
    from pathlib import Path

    AnalogLevelSensorUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )

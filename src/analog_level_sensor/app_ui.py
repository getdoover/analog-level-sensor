from pydoover import ui

from .app_tags import AnalogLevelSensorTags


class AnalogLevelSensorUI(ui.UI):
    percentage = ui.NumericVariable(
        "Level",
        units="%",
        value=AnalogLevelSensorTags.level_filled_percentage,
        precision=1,
        form=ui.Widget.radial,
        ranges=[
            ui.Range("Low", 0, 15, ui.Colour.blue),
            ui.Range("Good", 15, 100, ui.Colour.green),
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
        hidden="$config.app().hide_volume:boolean:true",
    )


def export():
    from pathlib import Path

    AnalogLevelSensorUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )

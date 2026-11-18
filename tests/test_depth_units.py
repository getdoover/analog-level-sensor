"""Tests for the Depth Units display conversion, in the shared config and UI,
through the processor (the device app's own are in device/src).

The level_reading tag stays canonical metres because peer apps consume it;
what the operator sees is converted into the configured depth unit instead.
"""

import asyncio

import pytest
from pydoover.config import NotSet

from analog_level_sensor_processor.app_config import AnalogLevelSensorProcessorConfig
from analog_level_sensor_processor.app_ui import AnalogLevelSensorProcessorUI
from common.common_config import DepthUnits


def _reset(element):
    element._value = NotSet
    children = getattr(element, "_elements", None)
    if isinstance(children, dict):
        children = children.values()
    for child in children or ():
        _reset(child)


@pytest.fixture(autouse=True)
def reset_config_elements():
    """pydoover holds config elements as class attributes, shared by every
    instance, so an injected value outlives the config that injected it."""
    for element in AnalogLevelSensorProcessorConfig()._element_map.values():
        _reset(element)


def make_config(**overrides):
    data = {
        "sensor_maximum_metres": 10.0,
        "sensor_minimum_metres": 0.0,
        "full_level": 10.0,
        "empty_level": 0.0,
        "sensor_minimum_ma": 4.0,
        "sensor_maximum_ma": 20.0,
        "sensor_type": "Submersible",
        "volume_curve": [],
        "hide_volume": False,
        "max_volume": 1000.0,
        "volume_units": "L",
    }
    data.update(overrides)

    config = AnalogLevelSensorProcessorConfig()
    config._inject_deployment_config(data)
    return config


def build_ui(config, ui_cls=AnalogLevelSensorProcessorUI):
    """Build a UI the way export() does, but against a real config."""
    ui = ui_cls(config, None, None)
    asyncio.run(ui.setup())
    return ui


@pytest.mark.parametrize(
    "unit,expected",
    [
        (DepthUnits.METRE, 2.5),
        (DepthUnits.CENTIMETRE, 250.0),
        (DepthUnits.MILLIMETRE, 2500.0),
        (DepthUnits.INCH, 98.425196850),
        (DepthUnits.FOOT, 8.2020997375),
    ],
)
def test_metres_convert_to_each_depth_unit(unit, expected):
    config = make_config(depth_units=unit)

    assert config.depth_unit == unit
    assert config.metres_to_depth_units(2.5) == pytest.approx(expected)


def test_conversion_passes_none_through():
    """An unavailable reading must stay unavailable, not become a zero."""
    assert make_config().metres_to_depth_units(None) is None


@pytest.mark.parametrize("stored", [None, "", "furlongs"])
def test_unknown_or_missing_unit_falls_back_to_metres(stored):
    """A legacy deployment config must not take the gauge down with it."""
    config = make_config(depth_units=stored)

    assert config.depth_unit == DepthUnits.METRE
    assert config.depth_unit_factor == 1.0
    assert config.depth_unit_precision == 2
    assert config.metres_to_depth_units(2.5) == 2.5


def test_precision_per_unit():
    assert make_config(depth_units="mm").depth_unit_precision == 0
    assert make_config(depth_units="ft").depth_unit_precision == 2


def _tag_binding(element):
    return element.to_dict()["currentValue"]


def test_level_gauge_uses_the_configured_unit_and_the_display_tag():
    ui = build_ui(make_config(depth_units="mm", reading_type="Level Reading"))
    element = ui.level_reading.to_dict()

    # the element key stays stable so existing dashboards keep working
    assert element["name"] == "level_reading"
    assert element["units"] == "mm"
    assert element["decPrecision"] == 0
    # the browser resolves the $tag reference, so the conversion has to arrive
    # pre-scaled on its own tag
    assert element["currentValue"] == "$tag.app().level_reading_display:number:null"
    assert [(r["min"], r["max"]) for r in element["ranges"]] == [
        (0, 1500.0),
        (1500.0, 10000.0),
    ]


def test_metre_gauge_keeps_its_binding_to_level_reading():
    """Metres need no conversion, so the binding stays put and the plot history
    already logged against level_reading is kept."""
    ui = build_ui(make_config(depth_units="m", reading_type="Level Reading"))
    element = ui.level_reading.to_dict()

    assert element["units"] == "m"
    assert element["decPrecision"] == 2
    assert element["currentValue"] == "$tag.app().level_reading:number:null"
    assert [(r["min"], r["max"]) for r in element["ranges"]] == [(0, 1.5), (1.5, 10.0)]


def test_legacy_layout_also_shows_the_configured_unit():
    """An explicit null Reading Type keeps the multi-reading layout, but the
    level reading in it still belongs in the configured unit."""
    ui = build_ui(make_config(depth_units="mm", reading_type=None))
    element = ui.level_reading.to_dict()

    assert element["units"] == "mm"
    assert element["decPrecision"] == 0
    assert element["currentValue"] == "$tag.app().level_reading_display:number:null"

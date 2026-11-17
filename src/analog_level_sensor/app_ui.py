from pydoover import ui

from common.common_config import DepthUnits, SensorType
from common.common_ui import CommonAnalogLevelSensorUI

from . import calibration
from .alarm import AlarmType
from .app_config import AlarmSource


def _alarm_slider(display_name: str, name: str, dual_slider: bool) -> ui.Slider:
    return ui.Slider(
        display_name,
        name=name,
        dual_slider=dual_slider,
        inverted=False,
        hidden=True,
    )


def _calibration_labels(low=4, high=20, units="mA", radar=False) -> dict[str, str]:
    """Input labels; the endpoints are the configured input range.

    The zero is the minimum level and the span the maximum level. A Radar reads
    inverted (common_app._sensor_percentage), so its zero is the level at the
    maximum input and its span the level at the minimum input.
    """
    units = f" {units}" if units else ""
    zero_at, span_at = (high, low) if radar else (low, high)
    return {
        calibration.ZERO.name: f"Zero - level at {zero_at:g}{units} (m)",
        calibration.SPAN.name: f"Span - level at {span_at:g}{units} (m)",
        calibration.DENSITY.name: "Fluid Density (kg/m³)",
    }


def _calibration_input(spec: calibration.CalibrationValue) -> ui.FloatInput:
    # The RPC handler validates the value (including zero < span); min / max
    # here only guide the input. The default follows the config in setup.
    return ui.FloatInput(
        _calibration_labels()[spec.name],
        name=spec.name,
        min_val=spec.minimum,
        max_val=spec.maximum,
    )


class AnalogLevelSensorDeviceUI(CommonAnalogLevelSensorUI):
    # A single slider reports a number and a dual slider reports [low, high], so
    # each mode gets its own element. One element toggling dual_slider would
    # leave behind a stored value of the wrong shape on every mode change.
    alarm_point = _alarm_slider("Alarm Point", "alarm_point", dual_slider=False)
    alarm_range = _alarm_slider("Allowed Range", "alarm_range", dual_slider=True)

    # Operator Sensor Calibration (calibration.py): hidden unless the config
    # field enables it, so an existing deployment's UI is unchanged.
    sensor_calibration = ui.Submodule(
        "Sensor Calibration",
        children=[
            *(_calibration_input(spec) for spec in calibration.VALUES),
            ui.Button(
                "Reset to configured values",
                name=calibration.RESET_ELEMENT,
                requires_confirm=True,
            ),
        ],
        hidden=True,
        name="sensor_calibration",
    )

    async def setup(self):
        await super().setup()
        self._setup_alarm()
        self._setup_calibration()

    def _setup_calibration(self):
        if not self.config.operator_calibration_enabled:
            return
        self.sensor_calibration.hidden = False
        radar = self.config.type.value == SensorType.RADAR
        try:
            labels = _calibration_labels(
                float(self.config.sensor_min_mA.value),
                float(self.config.sensor_max_mA.value),
                self.config.input_units.value or "",
                radar=radar,
            )
        except (TypeError, ValueError):
            labels = _calibration_labels(radar=radar)
        for spec in calibration.VALUES:
            element = getattr(self.sensor_calibration, spec.name)
            element.display_name = labels[spec.name]
            default = spec.config_default(self.config)
            if default is None:
                continue
            # Each input shows its config default until an operator sets one.
            element.default = round(default, calibration.DECIMALS)
            # pydoover bakes the class default into the element's currentValue
            # fallback ("$cmds.app().<name>::<default>"); keep it in step.
            element._value_location = f"$cmds.app().{spec.name}::{element.default}"

    def _setup_alarm(self):
        alarm_type = self.config.alarm_type
        enabled = self.config.alarm_enabled
        is_range = alarm_type is AlarmType.allowed_range
        is_level = self.config.alarm_source is AlarmSource.level

        self._scope_sliders_to_depth_unit()

        for slider in (self.alarm_point, self.alarm_range):
            slider.min_val = self.config.alarm_slider_min
            slider.max_val = self.config.alarm_slider_max
            slider.units = self.config.alarm_units
            if is_level:
                # Step with the unit's own resolution. The span is scaled by up
                # to 1000x, so leaving pydoover's 0.1 default would give a
                # millimetre slider 100,000 positions and hand the operator
                # setpoints like 6003.4 mm. Percentage and volume alarms are not
                # rescaled, so they keep the default.
                slider.step_size = float(10**-self.config.depth_unit_precision)

        self.alarm_point.hidden = not enabled or is_range
        self.alarm_range.hidden = not enabled or not is_range

        # An inverted slider shades [value, max] instead of [min, value]. Invert
        # for Less Than so the shaded band is the range that does not alarm, the
        # same way it already reads for Greater Than.
        self.alarm_point.inverted = alarm_type is AlarmType.less_than

        if alarm_type is AlarmType.greater_than:
            self.alarm_point.display_name = "High Alarm Point"
        elif alarm_type is AlarmType.less_than:
            self.alarm_point.display_name = "Low Alarm Point"

    def _scope_sliders_to_depth_unit(self):
        """Give a non-metre level alarm its own, per-unit element keys.

        The setpoint the operator drags is stored server-side against the
        element's key, and nothing in a deploy rescales it. Sharing one key
        across depth units would therefore silently reinterpret a stored 8.0 --
        set when the slider read 0-10 m -- as 8 mm the moment the slider
        switched to millimetres: a high alarm permanently breached by a
        half-full tank, or a low alarm that can never fire again.

        A per-unit key hands that install a fresh, unset slider instead.
        ``evaluate()`` treats an unset bound as "no bound", so the alarm stays
        quiet until the operator sets a point on the scale it is now shown in,
        and each unit keeps its own setpoint if the unit is later changed back.

        Metres keep the original keys, so existing metre installs are untouched,
        as are percentage and volume alarms -- neither of those sliders is
        rescaled by the depth unit.
        """
        if self.config.alarm_source is not AlarmSource.level:
            return

        unit = self.config.depth_unit
        if unit == DepthUnits.METRE:
            return

        self.alarm_point = self._scoped_slider(self.alarm_point, unit)
        self.alarm_range = self._scoped_slider(self.alarm_range, unit)

    @staticmethod
    def _scoped_slider(slider: ui.Slider, unit: str) -> ui.Slider:
        """A copy of ``slider`` keyed to ``unit``.

        Rebuilt rather than renamed in place because an Interaction derives the
        ``$cmds`` location it reads its stored value from at construction.
        """
        scoped = _alarm_slider(
            slider.display_name, f"{slider.name}_{unit}", slider.dual_slider
        )
        # keep the element where the declaration put it in the app's UI
        scoped.position = slider.position
        return scoped


AnalogLevelSensorUI = AnalogLevelSensorDeviceUI


def export():
    from pathlib import Path

    AnalogLevelSensorDeviceUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "analog_level_sensor"
    )

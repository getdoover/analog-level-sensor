//! The device app's UI — `common/common_ui.py` + `analog_level_sensor/app_ui.py`.
//!
//! [`DeviceUi::build`] is the class body (what `export` writes); [`setup`]
//! is the runtime mutation pydoover's `UI.setup()` makes from the config
//! before the schema is published.

use doover::tags::Tag;
use doover::ui::{
    Button, Colour, FloatInput, NumericVariable, Range, Slider, Submodule, UiBuild, UiElement,
    WarningIndicator, Widget,
};
use doover::Ui;
use serde_json::{json, Value};

use crate::calibration::{self, CalibrationValue};
use crate::config::{AlarmType, DepthUnit, DeviceConfig, ReadingType, SensorType};
use crate::tags::{DeviceTags, SENSOR_FAULT_DEFAULT_MESSAGE};
use crate::util::{format_g, json_dumps_ascii, round_dp};

/// Boundary between the "Low" and "Good" colour bands, as a fraction of the
/// gauge's full-scale value.
pub const LOW_BAND: f64 = 0.15;

/// A UI input whose commands are answered by an RPC handler the app
/// registers on `ui_cmds` itself (pydoover `@ui.handler(...,
/// auto_update=False)`), so it is not reported as an interaction: the
/// runner's default command path would write the raw value back and could
/// only answer an error as `INTERNAL_ERROR`. Its schema is the inner
/// element's, unchanged.
pub struct RpcInput<T>(pub T);

impl<T: UiElement> UiElement for RpcInput<T> {
    fn name(&self) -> &str {
        self.0.name()
    }
    fn position(&self) -> Option<i64> {
        self.0.position()
    }
    fn set_position_if_unset(&mut self, position: i64) {
        self.0.set_position_if_unset(position)
    }
    fn to_json(&self) -> Value {
        self.0.to_json()
    }
    fn is_interaction(&self) -> bool {
        false
    }
}

#[derive(Ui)]
pub struct DeviceUi {
    pub sensor_fault_warning: WarningIndicator,
    pub percentage: NumericVariable,
    pub level_reading: NumericVariable,
    pub volume: NumericVariable,
    pub alarm_point: Slider,
    pub alarm_range: Slider,
    pub sensor_calibration: Submodule,
}

fn alarm_slider(display_name: &str, name: &str, dual: bool) -> Slider {
    Slider::new(display_name).name(name).dual_slider(dual).inverted(false).hidden(true)
}

/// The calibration input labels; the endpoints are the configured input
/// range. A Radar reads inverted: its zero is the level at the maximum input.
pub fn calibration_labels(low: f64, high: f64, units: &str, radar: bool) -> [String; 3] {
    let units = if units.is_empty() { String::new() } else { format!(" {units}") };
    let (zero_at, span_at) = if radar { (high, low) } else { (low, high) };
    [
        format!("Zero - level at {}{units} (m)", format_g(zero_at)),
        format!("Span - level at {}{units} (m)", format_g(span_at)),
        "Fluid Density (kg/m³)".to_string(),
    ]
}

fn calibration_input(spec: &CalibrationValue, label: &str, default: Option<f64>) -> FloatInput {
    // The RPC handler validates the value; min / max only guide the input.
    let mut input = FloatInput::new(label).name(spec.name).min_val(spec.minimum).max_val(spec.maximum);
    if let Some(default) = default {
        // Each input shows its config default until an operator sets one
        // (`$cmds.app().<name>::<default>`).
        input = input.default(json!(default));
    }
    input
}

fn calibration_children(labels: &[String; 3], defaults: [Option<f64>; 3]) -> Vec<Box<dyn UiElement>> {
    let mut children: Vec<Box<dyn UiElement>> = calibration::VALUES
        .iter()
        .zip(labels)
        .zip(defaults)
        .map(|((spec, label), default)| {
            Box::new(RpcInput(calibration_input(spec, label, default))) as Box<dyn UiElement>
        })
        .collect();
    children.push(Box::new(RpcInput(
        Button::new("Reset to configured values").name(calibration::RESET_ELEMENT).requires_confirm(true),
    )));
    children
}

fn tag_ref<T: doover::tags::TagValue>(tag: &Tag<T>) -> String {
    tag.ui_reference()
}

impl UiBuild for DeviceUi {
    type Tags = DeviceTags;

    fn build(tags: &DeviceTags) -> Self {
        // Shown while sensor_fault is set; the schema is published once, so
        // the text and visibility are bound to tags. pydoover writes the
        // string default with json.dumps (ASCII-escaped).
        let mut warning = WarningIndicator::new("Sensor Fault Warning")
            .name("sensor_fault_warning")
            .can_cancel(false)
            .position(1);
        warning.interaction.element.display_name = format!(
            "$tag.app().{}:string:{}",
            tags.sensor_fault_message.name(),
            json_dumps_ascii(&json!(SENSOR_FAULT_DEFAULT_MESSAGE))
        );
        warning.interaction.element.hidden = Some(Value::String(tag_ref(&tags.sensor_fault_hidden)));

        Self {
            sensor_fault_warning: warning,
            percentage: NumericVariable::new("Level")
                .units("%")
                .value(&tags.level_filled_percentage)
                .precision(1)
                .form(Widget::RADIAL)
                .ranges(vec![
                    Range::new("Low", 0, LOW_BAND * 100.0, Colour::BLUE),
                    Range::new("Good", LOW_BAND * 100.0, 100, Colour::GREEN),
                ]),
            level_reading: NumericVariable::new("Level Reading")
                .units("m")
                .value(&tags.level_reading)
                .precision(2),
            volume: NumericVariable::new("Tank Volume")
                .name("volume")
                .units("L")
                .value(&tags.level_volume)
                .precision(0)
                .hidden(true),
            alarm_point: alarm_slider("Alarm Point", "alarm_point", false),
            alarm_range: alarm_slider("Allowed Range", "alarm_range", true),
            sensor_calibration: Submodule::new("Sensor Calibration")
                .name("sensor_calibration")
                .hidden(true)
                .children(calibration_children(&calibration_labels(4.0, 20.0, "mA", false), [None; 3])),
        }
    }
}

fn bands(max: f64) -> Vec<Range> {
    vec![
        Range::new("Low", 0, LOW_BAND * max, Colour::BLUE),
        Range::new("Good", LOW_BAND * max, max, Colour::GREEN),
    ]
}

/// `UI.setup()`: the runtime mutations made from the config before publish.
pub fn setup(ui: &mut DeviceUi, tags: &DeviceTags, config: &DeviceConfig) {
    // -- common_ui -------------------------------------------------------
    ui.volume.common.units = Some(config.raw.volume_units.clone());

    // Depth units: the browser resolves the $tag reference, so a non-metre
    // gauge points at the pre-converted display tag.
    ui.level_reading.common.units = Some(config.depth_unit().label().to_string());
    ui.level_reading.precision = Some(config.depth_unit_precision());
    if config.depth_unit() != DepthUnit::Metre {
        ui.level_reading.value = (&tags.level_reading_display).into();
    }

    match config.reading_type {
        None => setup_legacy_layout(ui, config),
        Some(rt) => setup_single_reading(ui, config, rt),
    }

    // -- app_ui ----------------------------------------------------------
    setup_alarm(ui, config);
    setup_calibration(ui, config);
}

fn promote_volume(ui: &mut DeviceUi, config: &DeviceConfig) {
    let max_vol = config.full_scale_volume();
    ui.volume.common.hidden = Some(Value::Bool(false));
    ui.volume.precision = Some(config.raw.volume_decimal_precision);
    ui.volume.common.form = Some(Widget::RADIAL.to_string());
    ui.volume.ranges = Some(bands(max_vol));
    ui.volume.common.position = Some(10);
}

fn setup_legacy_layout(ui: &mut DeviceUi, config: &DeviceConfig) {
    if config.raw.hide_volume {
        return;
    }
    // Volume becomes the primary gauge; the percentage a plain reading.
    promote_volume(ui, config);
    ui.percentage.common.form = None;
    ui.percentage.ranges = None;
    ui.percentage.common.position = Some(20);
    ui.level_reading.common.position = Some(30);
}

fn setup_single_reading(ui: &mut DeviceUi, config: &DeviceConfig, reading_type: ReadingType) {
    for el in [&mut ui.percentage, &mut ui.level_reading, &mut ui.volume] {
        el.common.hidden = Some(Value::Bool(true));
    }
    match reading_type {
        ReadingType::Percentage => {
            ui.percentage.common.hidden = Some(Value::Bool(false));
            ui.percentage.common.position = Some(10);
        }
        ReadingType::Volume => promote_volume(ui, config),
        ReadingType::Level => {
            // The gauge reads in the depth unit; scale the bands out of the
            // metres Full Level is configured in.
            let full = config.raw.full_level * config.depth_unit_factor();
            ui.level_reading.common.hidden = Some(Value::Bool(false));
            ui.level_reading.common.form = Some(Widget::RADIAL.to_string());
            if full > 0.0 {
                ui.level_reading.ranges = Some(bands(full));
            }
            ui.level_reading.common.position = Some(10);
        }
    }
}

fn setup_alarm(ui: &mut DeviceUi, config: &DeviceConfig) {
    let alarm_type = config.raw.alarm_type;
    let enabled = config.raw.alarm_enabled;
    let is_range = alarm_type == AlarmType::AllowedRange;
    let is_level = config.alarm_source() == ReadingType::Level;

    // A non-metre level alarm gets its own per-unit keys: a stored setpoint is
    // never rescaled, so sharing a key would reread 8.0 m as 8 mm. A fresh,
    // unset slider keeps the alarm quiet until set on the new scale; metres
    // keep the original keys. The position (assigned at build) is kept.
    if is_level && config.depth_unit() != DepthUnit::Metre {
        let unit = config.depth_unit().label();
        ui.alarm_point.interaction.element.name = format!("alarm_point_{unit}");
        ui.alarm_range.interaction.element.name = format!("alarm_range_{unit}");
    }

    for slider in [&mut ui.alarm_point, &mut ui.alarm_range] {
        slider.min_val = number(config.alarm_slider_min());
        slider.max_val = number(config.alarm_slider_max());
        slider.interaction.element.units = Some(config.alarm_units());
        if is_level {
            // Step with the unit's own resolution (a 0-10000 mm slider at
            // 0.1 would be 100,000 positions).
            slider.step_size = number(10f64.powi(-(config.depth_unit_precision() as i32)));
        }
    }
    ui.alarm_point.interaction.element.hidden = Some(Value::Bool(!enabled || is_range));
    ui.alarm_range.interaction.element.hidden = Some(Value::Bool(!enabled || !is_range));
    // Invert for Less Than so the shaded band is the range that does not alarm.
    ui.alarm_point.inverted = alarm_type == AlarmType::LessThan;
    match alarm_type {
        AlarmType::GreaterThan => ui.alarm_point.interaction.element.display_name = "High Alarm Point".into(),
        AlarmType::LessThan => ui.alarm_point.interaction.element.display_name = "Low Alarm Point".into(),
        AlarmType::AllowedRange => {}
    }
}

fn number(v: f64) -> serde_json::Number {
    serde_json::Number::from_f64(v).unwrap_or_else(|| serde_json::Number::from(0))
}

fn setup_calibration(ui: &mut DeviceUi, config: &DeviceConfig) {
    if !config.raw.operator_calibration_enabled {
        return;
    }
    ui.sensor_calibration.common.hidden = Some(Value::Bool(false));
    let labels = calibration_labels(
        config.raw.sensor_minimum_ma,
        config.raw.sensor_maximum_ma,
        config.input_units(),
        config.raw.sensor_type == SensorType::Radar,
    );
    let cal_config = calibration::CalibrationConfig::from_config(config);
    let defaults = calibration::VALUES
        .map(|spec| spec.config_default(&cal_config).map(|d| round_dp(d, calibration::DECIMALS)));
    // Rebuilt rather than edited: the children are type-erased. Their
    // positions are unset again, and the runner's finalize() reassigns the
    // same ones (the traversal order is unchanged).
    ui.sensor_calibration.children = calibration_children(&labels, defaults);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::tests::make_config;
    use doover::tags::TagsCollection;
    use doover::ui::UiTree;

    fn build(overrides: Value) -> (DeviceUi, Value) {
        let config = make_config(overrides);
        let tags = DeviceTags::detached();
        let mut ui = DeviceUi::build(&tags);
        ui.finalize();
        setup(&mut ui, &tags, &config);
        ui.finalize();
        let schema = ui.to_schema(&Default::default());
        (ui, schema["children"].clone())
    }

    fn alarm(extra: Value) -> Value {
        let mut a = json!({"alarm_enabled": true, "alarm_type": "Greater Than", "reading_type": "Level Reading"});
        for (k, v) in extra.as_object().cloned().unwrap_or_default() {
            a[k] = v;
        }
        a
    }

    #[test]
    fn level_gauge_uses_the_configured_unit_and_the_display_tag() {
        let (_, c) = build(json!({"depth_units": "mm", "reading_type": "Level Reading"}));
        let el = &c["level_reading"];
        assert_eq!(el["units"], "mm");
        assert_eq!(el["decPrecision"], 0);
        assert_eq!(el["currentValue"], "$tag.app().level_reading_display:number:null");
        assert_eq!(el["ranges"][0]["max"], json!(1500.0));
        assert_eq!(el["ranges"][1]["max"], json!(10000.0));
    }

    #[test]
    fn metre_gauge_keeps_its_binding_to_level_reading() {
        let (_, c) = build(json!({"depth_units": "m", "reading_type": "Level Reading"}));
        let el = &c["level_reading"];
        assert_eq!(el["units"], "m");
        assert_eq!(el["decPrecision"], 2);
        assert_eq!(el["currentValue"], "$tag.app().level_reading:number:null");
        assert_eq!(el["ranges"][0]["max"], json!(1.5));
        assert_eq!(el["ranges"][1]["max"], json!(10.0));
    }

    #[test]
    fn legacy_layout_also_shows_the_configured_unit() {
        let (_, c) = build(json!({"depth_units": "mm", "reading_type": null}));
        let el = &c["level_reading"];
        assert_eq!(el["units"], "mm");
        assert_eq!(el["currentValue"], "$tag.app().level_reading_display:number:null");
        // hide_volume false in make_config: volume promoted.
        assert_eq!(c["volume"]["position"], 10);
        assert_eq!(c["level"]["position"], 20);
        assert!(c["level"].get("form").is_none());
    }

    #[test]
    fn level_alarm_step_matches_the_units_own_resolution() {
        for (unit, step) in [("m", 0.01), ("cm", 0.1), ("mm", 1.0), ("in", 0.1), ("ft", 0.01)] {
            let (ui, _) = build(alarm(json!({"depth_units": unit})));
            assert_eq!(ui.alarm_point.to_json()["stepSize"], json!(step), "{unit}");
            assert_eq!(ui.alarm_range.to_json()["stepSize"], json!(step), "{unit}");
        }
    }

    #[test]
    fn percentage_alarm_keeps_the_default_step_and_keys() {
        let (ui, _) = build(alarm(json!({"reading_type": "Filled Percentage", "depth_units": "mm"})));
        assert_eq!(ui.alarm_point.to_json()["stepSize"], json!(0.1));
        assert_eq!(ui.alarm_point.interaction.element.name.as_str(), "alarm_point");
        assert_eq!(ui.alarm_range.interaction.element.name.as_str(), "alarm_range");
    }

    #[test]
    fn non_metre_level_alarm_gets_its_own_slider_keys() {
        let (_, c) = build(alarm(json!({"depth_units": "mm"})));
        let point = &c["alarm_point_mm"];
        assert_eq!(point["currentValue"], "$cmds.app().alarm_point_mm");
        assert_eq!(point["units"], "mm");
        assert_eq!((point["min"].clone(), point["max"].clone()), (json!(0.0), json!(10000.0)));
        assert_eq!(c["alarm_range_mm"]["currentValue"], "$cmds.app().alarm_range_mm");
        let (_, metres) = build(alarm(json!({"depth_units": "m"})));
        assert!(metres.get("alarm_point").is_some());
        // keeps its declared position
        assert_eq!(point["position"], metres["alarm_point"]["position"]);
    }

    #[test]
    fn each_depth_unit_gets_a_distinct_slider_key() {
        let keys: Vec<String> = ["m", "cm", "mm", "in", "ft"]
            .iter()
            .map(|u| build(alarm(json!({"depth_units": u}))).0.alarm_point.interaction.element.name.as_str().to_string())
            .collect();
        assert_eq!(keys[0], "alarm_point");
        let mut unique = keys.clone();
        unique.dedup();
        assert_eq!(unique.len(), 5);
    }

    #[test]
    fn alarm_mode_drives_visibility_and_inversion() {
        let (ui, _) = build(alarm(json!({"alarm_type": "Less Than"})));
        assert_eq!(ui.alarm_point.to_json()["hidden"], json!(false));
        assert_eq!(ui.alarm_range.to_json()["hidden"], json!(true));
        assert_eq!(ui.alarm_point.to_json()["isInverted"], json!(true));
        assert_eq!(ui.alarm_point.to_json()["displayString"], "Low Alarm Point");
    }

    #[test]
    fn off_hides_the_calibration_submodule() {
        let (_, c) = build(json!({}));
        assert_eq!(c["sensor_calibration"]["hidden"], json!(true));
    }

    #[test]
    fn on_shows_the_submodule_with_config_defaults() {
        let (_, c) = build(json!({
            "operator_calibration_enabled": true,
            "sensor_minimum_metres": 0.25,
            "sensor_maximum_metres": 6.0,
            "fluid_density": 1180.0,
        }));
        let sub = &c["sensor_calibration"];
        assert_eq!(sub["hidden"], json!(false));
        let kids = &sub["children"];
        assert_eq!(kids["zero_m"]["default"], json!(0.25));
        assert_eq!(kids["span_m"]["default"], json!(6.0));
        assert_eq!(kids["fluid_density"]["default"], json!(1180.0));
        assert_eq!(kids["zero_m"]["currentValue"], "$cmds.app().zero_m::0.25");
        assert_eq!(kids["zero_m"]["displayString"], "Zero - level at 4 mA (m)");
        assert_eq!(kids["span_m"]["displayString"], "Span - level at 20 mA (m)");
        assert_eq!(kids["reset_calibration"]["displayString"], "Reset to configured values");
        assert_eq!(kids["zero_m"]["min"], json!(-100.0));
        assert_eq!(kids["span_m"]["min"], json!(0.0));
        // positions survive the rebuild
        assert_eq!(kids["zero_m"]["position"], 57);
        assert_eq!(kids["reset_calibration"]["position"], 60);
    }

    #[test]
    fn labels_name_the_input_each_value_is_read_at() {
        for (ty, zero_at, span_at) in [("Submersible", 4, 20), ("Radar", 20, 4), ("Radar Inverted", 4, 20)] {
            let (_, c) = build(json!({"operator_calibration_enabled": true, "sensor_type": ty}));
            let kids = &c["sensor_calibration"]["children"];
            assert_eq!(kids["zero_m"]["displayString"], format!("Zero - level at {zero_at} mA (m)"));
            assert_eq!(kids["span_m"]["displayString"], format!("Span - level at {span_at} mA (m)"));
        }
    }

    #[test]
    fn density_input_defaults_to_water_without_a_config_density() {
        let (_, c) = build(json!({"operator_calibration_enabled": true, "fluid_density": null}));
        assert_eq!(c["sensor_calibration"]["children"]["fluid_density"]["default"], json!(1000.0));
    }

    #[test]
    fn calibration_inputs_are_not_runner_interactions() {
        let (ui, _) = build(json!({"operator_calibration_enabled": true}));
        let names = ui.interaction_names();
        assert!(names.contains(&"alarm_point".to_string()));
        assert!(!names.iter().any(|n| n == "zero_m" || n == "reset_calibration"));
    }
}

//! The deployment config — `common/common_config.py` +
//! `analog_level_sensor/app_config.py`.
//!
//! [`RawConfig`] is the `#[derive(Config)]` declaration, field for field in
//! the order pydoover exports them (the common fields, then the device ones,
//! then the application position). [`DeviceConfig`] wraps it to add what the
//! derive cannot express:
//!
//! - pydoover's explicit `position=` per element (doover-rs numbers elements by
//!   declaration order, and this schema has positions out of order and two
//!   pairs that share one), patched into the exported schema, so the export
//!   stays byte-identical to the Python app's (which it replaced);
//! - the raw-value fallbacks the Python properties apply: an explicit `null`
//!   (or empty) Reading Type means "legacy layout", which doover-rs's loader
//!   would otherwise turn into the default, and an unknown or empty Depth Units
//!   falls back to metres rather than failing the load;
//! - the derived properties (`depth_unit_factor`, `alarm_source`,
//!   `alarm_slider_min`, …).

use doover::config::{ApplicationPosition, ConfigSchema, SchemaModel};
use doover::error::Result;
use doover::{Config, ConfigEnum, ConfigObject};
use serde_json::Value;

/// `common_app.WATER_DENSITY` (kg/m³).
pub const WATER_DENSITY: f64 = 1000.0;

/// pydoover `VolumeCurvePoint(config.Object)`.
#[derive(Debug, Clone, ConfigObject)]
pub struct VolumeCurvePoint {
    /// Level / depth in metres
    pub level: f64,
    /// Volume in configured volume units
    pub volume: f64,
}

/// `common_config.ReadingType` — also the deprecated Alarm Source's choices.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ConfigEnum)]
pub enum ReadingType {
    #[config(rename = "Level Reading")]
    Level,
    #[config(rename = "Filled Percentage")]
    Percentage,
    #[config(rename = "Volume")]
    Volume,
}

/// `common_config.InputUnits`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ConfigEnum)]
pub enum InputUnits {
    #[config(rename = "mA")]
    Milliamps,
    #[config(rename = "V")]
    Volts,
    #[config(rename = "raw")]
    Raw,
}

impl InputUnits {
    pub fn label(self) -> &'static str {
        match self {
            InputUnits::Milliamps => "mA",
            InputUnits::Volts => "V",
            InputUnits::Raw => "raw",
        }
    }
}

/// `common_config.SensorType`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ConfigEnum)]
pub enum SensorType {
    Submersible,
    Radar,
    RadarInverted,
}

/// `common_config.DepthUnits`. The stored value doubles as the display label.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ConfigEnum)]
pub enum DepthUnit {
    #[config(rename = "m")]
    Metre,
    #[config(rename = "cm")]
    Centimetre,
    #[config(rename = "mm")]
    Millimetre,
    #[config(rename = "in")]
    Inch,
    #[config(rename = "ft")]
    Foot,
}

impl DepthUnit {
    pub const ALL: [DepthUnit; 5] = [
        DepthUnit::Metre,
        DepthUnit::Centimetre,
        DepthUnit::Millimetre,
        DepthUnit::Inch,
        DepthUnit::Foot,
    ];

    pub fn label(self) -> &'static str {
        match self {
            DepthUnit::Metre => "m",
            DepthUnit::Centimetre => "cm",
            DepthUnit::Millimetre => "mm",
            DepthUnit::Inch => "in",
            DepthUnit::Foot => "ft",
        }
    }

    /// `DEPTH_UNIT_FACTORS`: multiply canonical metres by this.
    pub fn factor(self) -> f64 {
        match self {
            DepthUnit::Metre => 1.0,
            DepthUnit::Centimetre => 100.0,
            DepthUnit::Millimetre => 1000.0,
            DepthUnit::Inch => 39.37007874,
            DepthUnit::Foot => 3.280839895,
        }
    }

    /// `DEPTH_UNIT_PRECISION`: decimal places that read sensibly at this scale.
    pub fn precision(self) -> i64 {
        match self {
            DepthUnit::Metre => 2,
            DepthUnit::Centimetre => 1,
            DepthUnit::Millimetre => 0,
            DepthUnit::Inch => 1,
            DepthUnit::Foot => 2,
        }
    }

    fn from_label(label: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|u| u.label() == label)
    }
}

/// `alarm.AlarmType`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ConfigEnum)]
pub enum AlarmType {
    GreaterThan,
    LessThan,
    AllowedRange,
}

/// The declared schema, in pydoover's export order. Descriptions are the doc
/// comments, one line each (a line break would become a `\n` in the schema).
#[derive(Debug, Clone, Config)]
pub struct RawConfig {
    /// Which reading to display and alarm on: the level/depth, the filled percentage, or the volume. Focuses the UI on that single reading and points the alarm at it.
    #[config(default = ReadingType::Percentage)]
    pub reading_type: ReadingType,
    /// Maximum sensor depth (m)
    pub sensor_maximum_metres: f64,
    /// Level reading when full (m)
    pub full_level: f64,
    /// Units of the raw analog input reading.
    #[config(default = InputUnits::Milliamps)]
    pub input_units: InputUnits,
    /// Minimum raw sensor input in the configured input units.
    #[config(
        title = "Sensor Minimum Input",
        name = "sensor_minimum_ma",
        default = 4.0
    )]
    pub sensor_minimum_ma: f64,
    /// Maximum raw sensor input in the configured input units.
    #[config(
        title = "Sensor Maximum Input",
        name = "sensor_maximum_ma",
        default = 20.0
    )]
    pub sensor_maximum_ma: f64,
    /// Minimum sensor depth (m). For submersible sensors, the sensor's mounting height above the tank floor (negative if below it)
    #[config(default = 0.0)]
    pub sensor_minimum_metres: f64,
    /// Level reading when empty (m)
    #[config(default = 0.0)]
    pub empty_level: f64,
    /// Type of sensor. Radar inverted reads like a submersible sensor.
    #[config(default = SensorType::Submersible)]
    pub sensor_type: SensorType,
    /// Density of the measured fluid (kg/m³). Submersible (pressure) sensors are ranged in metres of water, so readings are scaled by 1000 / density. Ignored for radar sensors. Defaults to water.
    #[config(default = 1000.0, min = 1.0)]
    pub fluid_density: f64,
    #[config(item_title = "Volume Curve Point")]
    pub volume_curve: Vec<VolumeCurvePoint>,
    /// Whether to hide the tank volume in the UI
    #[config(default = true)]
    pub hide_volume: bool,
    /// Maximum tank volume in the configured volume units, used when no volume curve is configured
    #[config(default = 100000.0)]
    pub max_volume: f64,
    /// Units to display the volume reading in (e.g. L, kL, gal)
    #[config(default = "L")]
    pub volume_units: String,
    /// Units for the Level Reading gauge, and, when Reading Type is Level Reading, for the alarm slider and alarm message. The level_reading tag itself stays in metres for peer consumers (e.g. the Petronash HMI); a copy converted to this unit is published as the level_reading_display tag. Changing this unit gives a level alarm a fresh, unset Alarm Point slider on the new scale rather than reinterpreting the old setpoint as the new unit: the alarm stays quiet until the point is set again. Each unit keeps its own setpoint, so changing back restores the previous one.
    #[config(default = DepthUnit::Metre)]
    pub depth_units: DepthUnit,
    /// Number of decimal places to show for the volume reading
    #[config(default = 0)]
    pub volume_decimal_precision: i64,

    /// Analog input pin number
    #[config(title = "AI Pin")]
    pub ai_pin: i64,
    /// Digital output pin to power the sensor
    pub power_pin: Option<i64>,
    /// How often to poll the sensor (Hz)
    #[config(default = 1.0)]
    pub polling_frequency: f64,

    /// Send a notification when the reading crosses the alarm point
    #[config(default = false)]
    pub alarm_enabled: bool,
    /// Greater Than and Less Than alarm on a single point. Allowed Range alarms when the reading falls outside a low/high band.
    #[config(default = AlarmType::GreaterThan)]
    pub alarm_type: AlarmType,
    /// Deprecated: superseded by Reading Type.
    #[config(default = ReadingType::Level, hidden, deprecated)]
    pub alarm_source: ReadingType,
    /// Lower bound of the alarm point slider, in the alarm's display units (the units shown on the slider; Depth Units for a level alarm). Defaults to the alarm source's minimum.
    pub alarm_slider_minimum: Option<f64>,
    /// Upper bound of the alarm point slider, in the alarm's display units (the units shown on the slider; Depth Units for a level alarm). Defaults to the alarm source's maximum.
    pub alarm_slider_maximum: Option<f64>,
    /// How long the reading must stay out of bounds before notifying
    #[config(title = "Alarm Grace Period (s)", default = 30.0, min = 0.0)]
    pub alarm_grace_period_s: f64,
    /// How often to re-notify while the alarm remains active
    #[config(title = "Alarm Re-notify Interval (s)", default = 900.0, min = 0.0)]
    pub alarm_renotify_interval_s: f64,

    /// Let operators set the zero (minimum level), span (maximum level) and fluid density on site, from the Sensor Calibration section of this app and the local HMI, without a redeploy. The Sensor Minimum Metres, Sensor Maximum Metres and Fluid Density above become the defaults that Reset to configured values returns to.
    #[config(
        title = "Operator Sensor Calibration",
        name = "operator_calibration_enabled",
        default = false
    )]
    pub operator_calibration_enabled: bool,

    // Only read at deploy time (drives the app's slot in the UI); a doc
    // comment here would override ApplicationPosition's canonical description.
    #[allow(dead_code)]
    pub position: ApplicationPosition,
}

/// pydoover's explicit `position=` for each element, by key.
const POSITIONS: &[(&str, u32)] = &[
    ("reading_type", 0),
    ("sensor_maximum_metres", 1),
    ("full_level", 2),
    ("input_units", 3),
    ("sensor_minimum_ma", 4),
    ("sensor_maximum_ma", 5),
    ("sensor_minimum_metres", 6),
    ("empty_level", 7),
    ("sensor_type", 10),
    ("fluid_density", 10),
    ("volume_curve", 11),
    ("hide_volume", 12),
    ("max_volume", 13),
    ("volume_units", 14),
    ("depth_units", 14),
    ("volume_decimal_precision", 15),
    ("ai_pin", 0),
    ("power_pin", 8),
    ("polling_frequency", 9),
    ("alarm_enabled", 17),
    ("alarm_type", 18),
    ("alarm_source", 19),
    ("alarm_slider_minimum", 20),
    ("alarm_slider_maximum", 21),
    ("alarm_grace_period_s", 22),
    ("alarm_renotify_interval_s", 23),
    ("operator_calibration_enabled", 24),
    ("dv_app_position", 16),
];

/// The device app's typed config: the declared fields plus the Python
/// properties derived from them.
#[derive(Debug, Clone)]
pub struct DeviceConfig {
    pub raw: RawConfig,
    /// `None` when the stored Reading Type is an explicit null or empty (a
    /// legacy install): the multi-reading layout and the deprecated Alarm
    /// Source apply. An omitted key resolves to the default, not `None`.
    pub reading_type: Option<ReadingType>,
}

impl ConfigSchema for DeviceConfig {
    fn schema() -> SchemaModel {
        let mut schema = RawConfig::schema();
        for element in &mut schema.elements {
            if let Some((_, position)) = POSITIONS.iter().find(|(key, _)| *key == element.name) {
                element.position = Some(*position);
            }
        }
        schema
    }

    fn from_value(v: &Value) -> Result<Self> {
        let mut v = v.clone();
        let mut reading_type_unset = false;
        if let Some(obj) = v.as_object_mut() {
            // pydoover: an explicit null (or "") Reading Type reads as None.
            if let Some(rt) = obj.get("reading_type") {
                if rt.is_null() || rt.as_str() == Some("") {
                    reading_type_unset = true;
                    obj.remove("reading_type");
                }
            }
            // `depth_unit`: a null, empty or unknown unit falls back to metres
            // rather than taking the gauge, the slider or the conversion down.
            let unknown_unit = obj
                .get("depth_units")
                .is_some_and(|u| u.as_str().and_then(DepthUnit::from_label).is_none());
            if unknown_unit {
                obj.remove("depth_units");
            }
        }
        let raw = RawConfig::from_value(&v)?;
        let reading_type = (!reading_type_unset).then_some(raw.reading_type);
        Ok(Self { raw, reading_type })
    }
}

impl DeviceConfig {
    pub fn depth_unit(&self) -> DepthUnit {
        self.raw.depth_units
    }

    pub fn depth_unit_factor(&self) -> f64 {
        self.depth_unit().factor()
    }

    pub fn depth_unit_precision(&self) -> i64 {
        self.depth_unit().precision()
    }

    pub fn metres_to_depth_units(&self, value: Option<f64>) -> Option<f64> {
        value.map(|v| v * self.depth_unit_factor())
    }

    /// `config_fluid_density`: a non-positive density is not one; water.
    pub fn config_fluid_density(&self) -> f64 {
        let density = self.raw.fluid_density;
        if density.is_finite() && density > 0.0 {
            density
        } else {
            WATER_DENSITY
        }
    }

    pub fn input_units(&self) -> &'static str {
        self.raw.input_units.label()
    }

    /// The alarm tracks the Reading Type; an unset one falls back to the
    /// deprecated Alarm Source so a legacy alarm is unchanged.
    pub fn alarm_source(&self) -> ReadingType {
        self.reading_type.unwrap_or(self.raw.alarm_source)
    }

    pub fn alarm_units(&self) -> String {
        match self.alarm_source() {
            ReadingType::Percentage => "%".to_string(),
            ReadingType::Volume => self.raw.volume_units.clone(),
            ReadingType::Level => self.depth_unit().label().to_string(),
        }
    }

    pub fn alarm_slider_min(&self) -> f64 {
        self.raw
            .alarm_slider_minimum
            .unwrap_or_else(|| self.alarm_source_range().0)
    }

    pub fn alarm_slider_max(&self) -> f64 {
        self.raw
            .alarm_slider_maximum
            .unwrap_or_else(|| self.alarm_source_range().1)
    }

    /// The source's own span, used when the slider bounds are left unset.
    fn alarm_source_range(&self) -> (f64, f64) {
        match self.alarm_source() {
            ReadingType::Percentage => (0.0, 100.0),
            ReadingType::Volume => (0.0, self.full_scale_volume()),
            ReadingType::Level => {
                let factor = self.depth_unit_factor();
                (
                    self.raw.sensor_minimum_metres * factor,
                    self.raw.sensor_maximum_metres * factor,
                )
            }
        }
    }

    /// The top of the volume curve when one is configured, else Max Volume
    /// (the volume gauge's and the volume alarm's full scale).
    pub fn full_scale_volume(&self) -> f64 {
        let curve = &self.raw.volume_curve;
        if curve.len() >= 2 {
            curve
                .iter()
                .map(|p| p.volume)
                .fold(f64::NEG_INFINITY, f64::max)
        } else {
            self.raw.max_volume
        }
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use serde_json::{json, Map};

    /// The deployment config the Python tests inject (`make_config`).
    pub fn make_config(overrides: Value) -> DeviceConfig {
        let mut data = json!({
            "ai_pin": 1,
            "sensor_maximum_metres": 10.0,
            "sensor_minimum_metres": 0.0,
            "full_level": 10.0,
            "empty_level": 0.0,
            "sensor_minimum_ma": 4.0,
            "sensor_maximum_ma": 20.0,
            "sensor_type": "Submersible",
            "volume_curve": [],
            "hide_volume": false,
            "max_volume": 1000.0,
            "volume_units": "L",
        });
        let obj: &mut Map<String, Value> = data.as_object_mut().unwrap();
        for (k, val) in overrides.as_object().cloned().unwrap_or_default() {
            obj.insert(k, val);
        }
        DeviceConfig::from_value(&data).expect("config loads")
    }

    #[test]
    fn metres_convert_to_each_depth_unit() {
        for (unit, expected) in [
            ("m", 2.5),
            ("cm", 250.0),
            ("mm", 2500.0),
            ("in", 98.425196850),
            ("ft", 8.2020997375),
        ] {
            let config = make_config(json!({"depth_units": unit}));
            assert_eq!(config.depth_unit().label(), unit);
            let got = config.metres_to_depth_units(Some(2.5)).unwrap();
            assert!((got - expected).abs() < 1e-6 * expected, "{unit}: {got}");
        }
    }

    #[test]
    fn conversion_passes_none_through() {
        assert_eq!(make_config(json!({})).metres_to_depth_units(None), None);
    }

    #[test]
    fn unknown_or_missing_unit_falls_back_to_metres() {
        for stored in [Value::Null, json!(""), json!("furlongs")] {
            let config = make_config(json!({"depth_units": stored}));
            assert_eq!(config.depth_unit(), DepthUnit::Metre);
            assert_eq!(config.depth_unit_factor(), 1.0);
            assert_eq!(config.depth_unit_precision(), 2);
            assert_eq!(config.metres_to_depth_units(Some(2.5)), Some(2.5));
        }
    }

    #[test]
    fn precision_per_unit() {
        assert_eq!(
            make_config(json!({"depth_units": "mm"})).depth_unit_precision(),
            0
        );
        assert_eq!(
            make_config(json!({"depth_units": "ft"})).depth_unit_precision(),
            2
        );
    }

    #[test]
    fn reading_type_null_is_legacy_and_omitted_is_the_default() {
        assert_eq!(
            make_config(json!({})).reading_type,
            Some(ReadingType::Percentage)
        );
        assert_eq!(
            make_config(json!({"reading_type": null})).reading_type,
            None
        );
        assert_eq!(make_config(json!({"reading_type": ""})).reading_type, None);
        assert_eq!(
            make_config(json!({"reading_type": "Volume"})).reading_type,
            Some(ReadingType::Volume)
        );
    }

    #[test]
    fn missing_or_bad_density_is_water() {
        assert_eq!(make_config(json!({})).config_fluid_density(), 1000.0);
        assert_eq!(
            make_config(json!({"fluid_density": null})).config_fluid_density(),
            1000.0
        );
        assert_eq!(
            make_config(json!({"fluid_density": 0})).config_fluid_density(),
            1000.0
        );
        assert_eq!(
            make_config(json!({"fluid_density": 1250})).config_fluid_density(),
            1250.0
        );
    }

    #[test]
    fn calibration_flag_defaults_off() {
        assert!(!make_config(json!({})).raw.operator_calibration_enabled);
        assert!(
            make_config(json!({"operator_calibration_enabled": true}))
                .raw
                .operator_calibration_enabled
        );
    }

    // -- the alarm properties (test_app_alarm.py) ------------------------------

    fn enabled_alarm(extra: Value) -> Value {
        let mut alarm = json!({"alarm_enabled": true, "alarm_type": "Greater Than"});
        for (k, v) in extra.as_object().cloned().unwrap_or_default() {
            alarm[k] = v;
        }
        alarm
    }

    #[test]
    fn specific_reading_type_overrides_the_legacy_alarm_source() {
        let config = make_config(enabled_alarm(
            json!({"alarm_source": "Volume", "reading_type": "Level Reading"}),
        ));
        assert_eq!(config.alarm_source(), ReadingType::Level);
        assert_eq!(config.alarm_units(), "m");
    }

    #[test]
    fn reading_type_sets_the_slider_bounds_and_units() {
        let config = make_config(enabled_alarm(json!({"reading_type": "Volume"})));
        assert_eq!(
            (config.alarm_slider_min(), config.alarm_slider_max()),
            (0.0, 1000.0)
        );
        assert_eq!(config.alarm_units(), "L");
    }

    #[test]
    fn slider_bounds_default_to_the_sources_own_span() {
        let level = make_config(enabled_alarm(json!({"reading_type": null})));
        assert_eq!(
            (level.alarm_slider_min(), level.alarm_slider_max()),
            (0.0, 10.0)
        );
        assert_eq!(level.alarm_units(), "m");

        let pct = make_config(enabled_alarm(
            json!({"alarm_source": "Filled Percentage", "reading_type": null}),
        ));
        assert_eq!(
            (pct.alarm_slider_min(), pct.alarm_slider_max()),
            (0.0, 100.0)
        );
        assert_eq!(pct.alarm_units(), "%");

        let vol = make_config(enabled_alarm(
            json!({"alarm_source": "Volume", "reading_type": null}),
        ));
        assert_eq!(
            (vol.alarm_slider_min(), vol.alarm_slider_max()),
            (0.0, 1000.0)
        );
        assert_eq!(vol.alarm_units(), "L");
    }

    #[test]
    fn explicit_slider_bounds_override_the_source_span() {
        let config = make_config(enabled_alarm(
            json!({"alarm_slider_minimum": 2.0, "alarm_slider_maximum": 8.0}),
        ));
        assert_eq!(
            (config.alarm_slider_min(), config.alarm_slider_max()),
            (2.0, 8.0)
        );
    }

    #[test]
    fn volume_source_full_scale_comes_from_the_curve_when_present() {
        let config = make_config(enabled_alarm(json!({
            "alarm_source": "Volume",
            "reading_type": null,
            "volume_curve": [{"level": 0.0, "volume": 0.0}, {"level": 10.0, "volume": 7500.0}],
        })));
        assert_eq!(config.alarm_slider_max(), 7500.0);
    }

    #[test]
    fn alarm_type_parses_and_null_reading_type_falls_back_to_level() {
        let config = make_config(enabled_alarm(
            json!({"alarm_type": "Allowed Range", "reading_type": null}),
        ));
        assert_eq!(config.raw.alarm_type, AlarmType::AllowedRange);
        assert_eq!(config.alarm_source(), ReadingType::Level);
    }

    #[test]
    fn deployment_config_without_alarm_keys_reads_as_disabled() {
        let config = make_config(json!({}));
        assert!(!config.raw.alarm_enabled);
        assert_eq!(config.raw.alarm_grace_period_s, 30.0);
        assert_eq!(config.raw.alarm_renotify_interval_s, 900.0);
        assert_eq!(config.raw.alarm_type, AlarmType::GreaterThan);
        // No reading_type key: the "Filled Percentage" default drives it.
        assert_eq!(config.alarm_source(), ReadingType::Percentage);
    }

    #[test]
    fn level_slider_span_and_units_follow_the_depth_units() {
        let config = make_config(enabled_alarm(
            json!({"reading_type": "Level Reading", "depth_units": "mm"}),
        ));
        assert_eq!(config.alarm_units(), "mm");
        assert_eq!(
            (config.alarm_slider_min(), config.alarm_slider_max()),
            (0.0, 10000.0)
        );
    }

    #[test]
    fn explicit_slider_bounds_are_taken_as_display_units() {
        let config = make_config(enabled_alarm(json!({
            "alarm_slider_minimum": 1000.0,
            "alarm_slider_maximum": 8000.0,
            "reading_type": "Level Reading",
            "depth_units": "mm",
        })));
        assert_eq!(
            (config.alarm_slider_min(), config.alarm_slider_max()),
            (1000.0, 8000.0)
        );
    }
}

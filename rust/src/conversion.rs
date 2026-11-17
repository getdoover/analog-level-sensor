//! The shared conversion and sensor-fault handling —
//! `common/common_app.py` (`CommonAnalogLevelSensorApplication`).
//!
//! Pure over the config, the calibration values in effect ([`Params`]) and a
//! [`TagIo`] the published tags go through, so it is testable without a
//! device agent.

use serde_json::Value;

use crate::config::{DeviceConfig, SensorType, WATER_DENSITY};
use crate::tags::{self, TagIo};
use crate::util::round_dp;

/// Loop-current check, relative to the configured minimum input (4 mA).
/// Below the minimum is a healthy sensor at the end of its range and reads as
/// the minimum. Only a dead loop is a fault: a reading this fraction of the
/// input span below the minimum (under 1 mA on 4-20 mA).
pub const UNDER_RANGE_MARGIN: f64 = 3.0 / 16.0;
/// Consecutive samples needed to enter, and to leave, a sensor fault.
pub const FAULT_DEBOUNCE_SAMPLES: u32 = 3;

/// The three values the 4-20 mA conversion runs on: the deployment config,
/// or the operator's calibration when Operator Sensor Calibration is on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Params {
    /// The minimum level (m): at the minimum input, or at the maximum input
    /// for a Radar (which reads inverted). May be negative.
    pub zero_m: f64,
    /// The maximum level (m).
    pub span_m: f64,
    /// kg/m³; scales a submersible reading.
    pub fluid_density: f64,
}

impl Params {
    pub fn from_config(config: &DeviceConfig) -> Self {
        Self {
            zero_m: config.raw.sensor_minimum_metres,
            span_m: config.raw.sensor_maximum_metres,
            fluid_density: config.config_fluid_density(),
        }
    }
}

/// The conversion for one sample: the config plus the values in effect.
pub struct Conversion<'a> {
    pub config: &'a DeviceConfig,
    pub params: Params,
}

impl<'a> Conversion<'a> {
    pub fn new(config: &'a DeviceConfig, params: Params) -> Self {
        Self { config, params }
    }

    fn map_value(&self, value: f64, low_a: f64, high_a: f64, low_b: f64, high_b: f64, invert: bool) -> f64 {
        if invert && self.config.raw.sensor_type == SensorType::Radar {
            return (high_b - low_b) - ((value - low_a) / (high_a - low_a)) * (high_b - low_b);
        }
        ((value - low_a) / (high_a - low_a)) * (high_b - low_b) + low_b
    }

    fn sensor_percentage(&self, reading: f64) -> f64 {
        let raw = &self.config.raw;
        self.map_value(reading, raw.sensor_minimum_ma, raw.sensor_maximum_ma, 0.0, 100.0, true)
    }

    /// Level in canonical metres.
    pub fn level_reading(&self, reading: f64) -> f64 {
        let perc = self.sensor_percentage(reading);
        let zero = self.params.zero_m;
        let mut level = self.map_value(perc, 0.0, 100.0, zero, self.params.span_m, false);
        if self.config.raw.sensor_type == SensorType::Submersible {
            // Scale only the fluid column; the zero is the mounting height and
            // does not depend on the fluid.
            level = zero + (level - zero) * WATER_DENSITY / self.params.fluid_density;
        }
        level
    }

    fn curve_points(&self) -> Vec<(f64, f64)> {
        let mut points: Vec<(f64, f64)> =
            self.config.raw.volume_curve.iter().map(|p| (p.level, p.volume)).collect();
        points.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        points
    }

    pub fn filled_percentage(&self, reading: f64) -> Option<f64> {
        let lev = self.level_reading(reading);
        let raw = &self.config.raw;
        if raw.volume_curve.len() < 2 {
            return Some(self.map_value(lev, raw.empty_level, raw.full_level, 0.0, 100.0, false));
        }
        let vol = get_volume(lev, &self.curve_points())?;
        let max_vol = raw.volume_curve.iter().map(|p| p.volume).fold(f64::NEG_INFINITY, f64::max);
        Some(round_dp(vol / max_vol * 100.0, 3))
    }

    pub fn volume(&self, reading: f64) -> Option<f64> {
        if self.config.raw.volume_curve.len() >= 2 {
            return get_volume(self.level_reading(reading), &self.curve_points());
        }
        let perc = self.filled_percentage(reading)?;
        Some(self.config.raw.max_volume * (perc / 100.0))
    }
}

/// `_get_volume`: interpolate within the (sorted) curve, extrapolating off
/// the nearest end segment outside it, so misconfiguration shows up rather
/// than being hidden.
pub fn get_volume(level: f64, points: &[(f64, f64)]) -> Option<f64> {
    if points.is_empty() {
        return None;
    }
    for pair in points.windows(2) {
        let ((x1, y1), (x2, y2)) = (pair[0], pair[1]);
        if x1 <= level && level <= x2 {
            return Some(y1 + (level - x1) * (y2 - y1) / (x2 - x1));
        }
    }
    if points.len() < 2 {
        // pydoover would raise on a one-point curve here; callers only reach
        // this with two or more points.
        return None;
    }
    let ((x1, y1), (x2, y2)) = if level < points[0].0 {
        (points[0], points[1])
    } else {
        (points[points.len() - 2], points[points.len() - 1])
    };
    Some(y1 + (level - x1) * (y2 - y1) / (x2 - x1))
}

/// The debounced under-range (dead loop) check and what it published.
#[derive(Debug, Clone)]
pub struct FaultTracker {
    /// `None` until the first sample, which reads the published tag.
    active: Option<bool>,
    streak: u32,
    input: Option<f64>,
    pub debounce_samples: u32,
}

impl Default for FaultTracker {
    fn default() -> Self {
        Self { active: None, streak: 0, input: None, debounce_samples: FAULT_DEBOUNCE_SAMPLES }
    }
}

impl FaultTracker {
    fn in_fault(&mut self, tags: &dyn TagIo) -> bool {
        *self.active.get_or_insert_with(|| {
            // A fault published before a restart still stands, so it still
            // takes good samples to clear it.
            tags.get(tags::SENSOR_FAULT).is_some_and(|v| !v.is_null())
        })
    }

    /// Debounce the under-range check. True while the input is in fault.
    fn update(&mut self, config: &DeviceConfig, result: f64, under_range: bool, tags: &dyn TagIo) -> bool {
        let in_fault = self.in_fault(tags);
        if under_range {
            self.input = Some(result);
        }
        if under_range == in_fault {
            self.streak = 0;
            return in_fault;
        }
        self.streak += 1;
        if self.streak < self.debounce_samples {
            return in_fault;
        }
        self.streak = 0;
        self.active = Some(under_range);
        let units = config.input_units();
        if under_range {
            tracing::warn!(
                "Sensor input {} {units} is below range (minimum {} {units}): level, \
                 percentage and volume cleared until it recovers",
                crate::util::py_float(result),
                crate::util::py_float(config.raw.sensor_minimum_ma),
            );
        } else {
            tracing::info!("Sensor input back in range at {} {units}", crate::util::py_float(result));
        }
        under_range
    }

    fn message(&self, config: &DeviceConfig) -> String {
        match self.input {
            None => tags::SENSOR_FAULT_DEFAULT_MESSAGE.to_string(),
            Some(value) => format!(
                "Sensor signal below range ({value:.2} {}) — check the sensor and its wiring",
                config.input_units()
            ),
        }
    }
}

/// How far below the minimum input a reading still counts as the minimum.
fn under_range_margin(config: &DeviceConfig) -> f64 {
    (config.raw.sensor_maximum_ma - config.raw.sensor_minimum_ma).abs() * UNDER_RANGE_MARGIN
}

/// `CommonAnalogLevelSensorApplication.handle_update`: publish one raw input
/// sample. Returns the reading the level, percentage and volume were derived
/// from (the sample, raised to the minimum input in the clamp band), or `None`
/// when no level was published: no reading, a sensor fault, or an under-range
/// sample that has not yet become one.
pub fn handle_update(
    config: &DeviceConfig,
    params: Params,
    fault: &mut FaultTracker,
    result: Option<f64>,
    tags: &mut dyn TagIo,
) -> Option<f64> {
    // pydoover logs this at INFO; at 40 Hz that is most of a CM4 core's worth
    // of formatting and log I/O, so it is debug here.
    tracing::debug!("Level sensor reading: {result:?}");
    let result = result?;

    // Always, a fault included: it is the one number that says what the
    // sensor is doing.
    tags.set(tags::RAW_LEVEL_READING, Value::from(result));

    let minimum = config.raw.sensor_minimum_ma;
    let under_range = result < minimum - under_range_margin(config);
    if fault.update(config, result, under_range, tags) {
        // Clear the derived values rather than leave the last good ones
        // looking live to peers and UIs.
        for name in [
            tags::LEVEL_FILLED_PERCENTAGE,
            tags::LEVEL_READING,
            tags::LEVEL_READING_DISPLAY,
            tags::LEVEL_VOLUME,
        ] {
            tags.set(name, Value::Null);
        }
        tags.set(tags::SENSOR_FAULT, Value::from(tags::SENSOR_FAULT_UNDER_RANGE));
        tags.set(tags::SENSOR_FAULT_MESSAGE, Value::from(fault.message(config)));
        tags.set(tags::SENSOR_FAULT_HIDDEN, Value::Bool(false));
        return None;
    }
    if under_range {
        // Not a fault yet: hold the values already published.
        return None;
    }

    // Within the margin below the minimum is a healthy sensor at the end of
    // its range, so it reads as the minimum input.
    let reading = result.max(minimum);
    let conv = Conversion::new(config, params);
    let level = conv.level_reading(reading);

    tags.set(tags::LEVEL_FILLED_PERCENTAGE, opt(conv.filled_percentage(reading)));
    // level_reading is canonical metres for peer apps; the configured-unit
    // copy is published alongside it for this app's own gauge.
    tags.set(tags::LEVEL_READING, Value::from(level));
    tags.set(tags::LEVEL_READING_DISPLAY, opt(config.metres_to_depth_units(Some(level))));
    // Always publish volume so peers can show it; hide_volume and Reading Type
    // only affect this app's own gauge.
    tags.set(tags::LEVEL_VOLUME, opt(conv.volume(reading)));
    tags.set(tags::SENSOR_FAULT, Value::Null);
    tags.set(tags::SENSOR_FAULT_HIDDEN, Value::Bool(true));
    Some(reading)
}

fn opt(v: Option<f64>) -> Value {
    v.map(Value::from).unwrap_or(Value::Null)
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::config::tests::make_config;
    use crate::tags::tests::FakeTags;
    use serde_json::json;

    /// `FakeApp` in test_common_app.py: config + fault state + tags.
    pub struct App {
        pub config: DeviceConfig,
        pub fault: FaultTracker,
        pub tags: FakeTags,
    }

    impl App {
        pub fn new(overrides: Value) -> Self {
            let mut base = json!({
                "sensor_maximum_metres": 10.0,
                "full_level": 10.0,
                "hide_volume": false,
                "max_volume": 1000.0,
            });
            for (k, v) in overrides.as_object().cloned().unwrap_or_default() {
                base[k] = v;
            }
            Self { config: make_config(base), fault: FaultTracker::default(), tags: FakeTags::default() }
        }

        pub fn feed(&mut self, readings: &[f64]) {
            for r in readings {
                self.update(Some(*r));
            }
        }

        pub fn update(&mut self, r: Option<f64>) -> Option<f64> {
            let params = Params::from_config(&self.config);
            handle_update(&self.config, params, &mut self.fault, r, &mut self.tags)
        }

        pub fn conv(&self) -> Conversion<'_> {
            Conversion::new(&self.config, Params::from_config(&self.config))
        }

        pub fn levels(&self) -> Vec<Value> {
            LEVEL_TAGS.iter().map(|n| self.tags.value(n)).collect()
        }
    }

    const UNDER_RANGE: f64 = 0.5;
    const LEVEL_TAGS: [&str; 4] = [
        tags::LEVEL_FILLED_PERCENTAGE,
        tags::LEVEL_READING,
        tags::LEVEL_READING_DISPLAY,
        tags::LEVEL_VOLUME,
    ];

    fn nulls() -> Vec<Value> {
        vec![Value::Null; 4]
    }

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-6 * b.abs().max(1.0)
    }

    #[test]
    fn handle_update_writes_level_tags() {
        let mut app = App::new(json!({}));
        app.update(Some(12.0));
        assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), json!(12.0));
        assert_eq!(app.tags.value(tags::LEVEL_READING), json!(5.0));
        assert_eq!(app.tags.value(tags::LEVEL_FILLED_PERCENTAGE), json!(50.0));
        assert_eq!(app.tags.value(tags::LEVEL_VOLUME), json!(500.0));
        assert_eq!(app.tags.value(tags::LEVEL_READING_DISPLAY), json!(5.0));
    }

    #[test]
    fn the_debounce_is_three_samples() {
        assert_eq!(FAULT_DEBOUNCE_SAMPLES, 3);
    }

    #[test]
    fn clamp_band_reads_as_empty_not_a_fault() {
        for reading in [3.9, 3.8, 3.95] {
            let mut app = App::new(json!({}));
            app.feed(&[reading; 5]);
            assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
            assert_eq!(app.tags.value(tags::LEVEL_FILLED_PERCENTAGE), json!(0.0));
            assert_eq!(app.tags.value(tags::LEVEL_READING), json!(0.0));
            assert_eq!(app.tags.value(tags::LEVEL_VOLUME), json!(0.0));
            assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), json!(reading));
            assert_eq!(app.tags.value(tags::SENSOR_FAULT_HIDDEN), json!(true));
        }
    }

    #[test]
    fn steady_under_range_faults_after_three_samples() {
        let mut app = App::new(json!({}));
        app.feed(&[UNDER_RANGE, UNDER_RANGE]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        app.feed(&[UNDER_RANGE; 11]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), json!("under_range"));
        assert_eq!(app.levels(), nulls());
        assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), json!(UNDER_RANGE));
        assert_eq!(app.tags.value(tags::SENSOR_FAULT_HIDDEN), json!(false));
        assert_eq!(
            app.tags.value(tags::SENSOR_FAULT_MESSAGE),
            json!("Sensor signal below range (0.50 mA) — check the sensor and its wiring")
        );
    }

    #[test]
    fn a_fault_after_good_readings_clears_the_stale_level() {
        let mut app = App::new(json!({}));
        app.update(Some(12.0));
        assert_eq!(app.levels(), vec![json!(50.0), json!(5.0), json!(5.0), json!(500.0)]);
        app.feed(&[UNDER_RANGE; 3]);
        assert_eq!(app.levels(), nulls());
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), json!("under_range"));
    }

    #[test]
    fn a_single_spike_does_not_fault_and_holds_the_level() {
        let mut app = App::new(json!({}));
        app.update(Some(12.0));
        app.feed(&[0.5, 12.0, 0.5, 0.5, 12.0, 0.5]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        assert_eq!(app.levels(), vec![json!(50.0), json!(5.0), json!(5.0), json!(500.0)]);
        assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), json!(0.5));
    }

    #[test]
    fn recovery_needs_three_good_samples() {
        let mut app = App::new(json!({}));
        app.feed(&[UNDER_RANGE; 3]);
        app.feed(&[12.0, 12.0, UNDER_RANGE, 12.0, 12.0]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), json!("under_range"));
        assert_eq!(app.levels(), nulls());
        assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), json!(12.0));
        app.update(Some(12.0));
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT_HIDDEN), json!(true));
        assert_eq!(app.levels(), vec![json!(50.0), json!(5.0), json!(5.0), json!(500.0)]);
    }

    #[test]
    fn clamp_band_samples_count_towards_recovery() {
        let mut app = App::new(json!({}));
        app.feed(&[UNDER_RANGE; 3]);
        app.feed(&[3.9; 3]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        assert_eq!(app.tags.value(tags::LEVEL_FILLED_PERCENTAGE), json!(0.0));
    }

    #[test]
    fn a_fault_published_before_a_restart_still_needs_good_samples() {
        let mut app = App::new(json!({}));
        app.tags.store.insert(tags::SENSOR_FAULT.into(), json!("under_range"));
        app.feed(&[12.0, 12.0]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), json!("under_range"));
        assert_eq!(app.tags.value(tags::LEVEL_READING), Value::Null);
        app.update(Some(12.0));
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        assert_eq!(app.tags.value(tags::LEVEL_READING), json!(5.0));
    }

    #[test]
    fn no_reading_publishes_nothing() {
        let mut app = App::new(json!({}));
        assert_eq!(app.update(None), None);
        assert_eq!(app.tags.value(tags::RAW_LEVEL_READING), Value::Null);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
    }

    #[test]
    fn fault_threshold_follows_the_configured_minimum() {
        let mut app = App::new(json!({"sensor_minimum_ma": 0.0}));
        app.feed(&[0.0; 3]);
        assert_eq!(app.tags.value(tags::SENSOR_FAULT), Value::Null);
        assert_eq!(app.tags.value(tags::LEVEL_FILLED_PERCENTAGE), json!(0.0));
    }

    #[test]
    fn volume_is_published_even_when_hidden() {
        let mut app = App::new(json!({"hide_volume": true}));
        app.update(Some(12.0));
        assert_eq!(app.tags.value(tags::LEVEL_VOLUME), json!(500.0));
    }

    #[test]
    fn display_tag_is_converted_while_level_reading_stays_metres() {
        let mut app = App::new(json!({"depth_units": "mm"}));
        app.update(Some(12.0));
        assert_eq!(app.tags.value(tags::LEVEL_READING), json!(5.0));
        assert_eq!(app.tags.value(tags::LEVEL_READING_DISPLAY), json!(5000.0));
    }

    #[test]
    fn level_defaults_to_water_when_density_missing() {
        assert_eq!(App::new(json!({})).conv().level_reading(12.0), 5.0);
    }

    #[test]
    fn submersible_level_scaled_by_fluid_density() {
        let app = App::new(json!({"fluid_density": 1250.0}));
        assert!(approx(app.conv().level_reading(12.0), 4.0));
    }

    #[test]
    fn radar_level_ignores_fluid_density() {
        let app = App::new(json!({"fluid_density": 1250.0, "sensor_type": "Radar Inverted"}));
        assert_eq!(app.conv().level_reading(12.0), 5.0);
    }

    #[test]
    fn density_scaling_leaves_mounting_offset_alone() {
        let app = App::new(json!({
            "fluid_density": 1250.0,
            "sensor_minimum_metres": 0.015,
            "sensor_maximum_metres": 10.015,
        }));
        assert!(approx(app.conv().level_reading(12.0), 4.015));
    }

    const CURVE: [(f64, f64); 3] = [(0.0, 0.0), (0.1, 77.66), (0.83, 1019.76)];

    #[test]
    fn volume_interpolates_within_the_curve() {
        assert!(approx(get_volume(0.05, &CURVE).unwrap(), 38.83));
    }

    #[test]
    fn volume_extrapolates_past_the_curve_ends() {
        assert!(get_volume(0.9, &CURVE).unwrap() > 1019.76);
        assert!(get_volume(-0.1, &CURVE).unwrap() < 0.0);
    }

    #[test]
    fn volume_curve_order_does_not_matter() {
        let app = App::new(json!({"volume_curve": [
            {"level": 0.83, "volume": 1019.76},
            {"level": 0.0, "volume": 0.0},
            {"level": 0.1, "volume": 77.66},
        ]}));
        let conv = app.conv();
        assert!(approx(get_volume(0.05, &conv.curve_points()).unwrap(), 38.83));
    }
}

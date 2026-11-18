//! The alarm's wiring into the device app — the `_alarm_value` /
//! `_alarm_bounds` / `_check_alarm` / `_alarm_message` half of
//! `analog_level_sensor/application.py`, pure over the slider values read
//! from ui_cmds.

use serde_json::Value;

use crate::alarm::{evaluate, Alarm, Breach};
use crate::config::{AlarmType, DeviceConfig, ReadingType};
use crate::conversion::Conversion;
use crate::util::{format_g, round_dp};

/// The quantity the alarm tracks, derived from the raw reading. A level alarm
/// compares in the configured depth unit (the slider and message use it).
pub fn alarm_value(conv: &Conversion<'_>, reading: f64) -> Option<f64> {
    match conv.config.alarm_source() {
        ReadingType::Percentage => conv.filled_percentage(reading),
        ReadingType::Volume => conv.volume(reading),
        ReadingType::Level => conv
            .config
            .metres_to_depth_units(Some(conv.level_reading(reading))),
    }
}

/// The alarm setpoint(s) from whichever slider the mode uses: `(point, low,
/// high)`, each `None` while the operator has not set it. A dual slider
/// reports `[low, high]`, not always in that order.
pub fn alarm_bounds(
    config: &DeviceConfig,
    point: Option<&Value>,
    range: Option<&Value>,
) -> (Option<f64>, Option<f64>, Option<f64>) {
    if config.raw.alarm_type == AlarmType::AllowedRange {
        let pair = range
            .and_then(Value::as_array)
            .filter(|a| a.len() == 2)
            .and_then(|a| Some((a[0].as_f64()?, a[1].as_f64()?)));
        return match pair {
            Some((a, b)) => (None, Some(a.min(b)), Some(a.max(b))),
            None => (None, None, None),
        };
    }
    (point.and_then(Value::as_f64), None, None)
}

/// `_check_alarm`: the notification message to send now, if any.
pub fn check_alarm(
    alarm: &mut Alarm,
    conv: &Conversion<'_>,
    reading: f64,
    point: Option<&Value>,
    range: Option<&Value>,
    display_name: &str,
) -> Option<String> {
    check_alarm_at(alarm, conv, reading, point, range, display_name, None)
}

/// [`check_alarm`] with an explicit alarm clock (tests).
pub fn check_alarm_at(
    alarm: &mut Alarm,
    conv: &Conversion<'_>,
    reading: f64,
    point: Option<&Value>,
    range: Option<&Value>,
    display_name: &str,
    now: Option<f64>,
) -> Option<String> {
    let config = conv.config;
    if !config.raw.alarm_enabled {
        alarm.clear();
        return None;
    }
    let (point, low, high) = alarm_bounds(config, point, range);
    let value = alarm_value(conv, reading);
    let breach = evaluate(value, config.raw.alarm_type, point, low, high);
    let notify = match now {
        Some(now) => alarm.update_at(breach, now),
        None => alarm.update(breach),
    };
    match (notify, breach, value) {
        (true, Some(breach), Some(value)) => {
            Some(alarm_message(config, display_name, value, breach))
        }
        _ => None,
    }
}

/// Python `f"{round(value, 2):g}"`.
pub fn format_value(value: f64) -> String {
    format_g(round_dp(value, 2))
}

pub fn alarm_message(
    config: &DeviceConfig,
    display_name: &str,
    reading: f64,
    breach: Breach,
) -> String {
    let units = config.alarm_units();
    let suffix = if units.is_empty() {
        String::new()
    } else {
        format!(" {units}")
    };
    format!(
        "{display_name} has {} {}{suffix} with a value of {}{suffix}",
        breach.direction.as_str(),
        format_value(breach.bound),
        format_value(reading),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::tests::make_config;
    use crate::conversion::Params;
    use serde_json::json;

    const MID_SCALE_MA: f64 = 12.0;

    fn enabled(extra: Value) -> Value {
        let mut a = json!({"alarm_enabled": true, "alarm_type": "Greater Than"});
        for (k, v) in extra.as_object().cloned().unwrap_or_default() {
            a[k] = v;
        }
        a
    }

    /// `StubApp._check_alarm` with grace 0 / renotify 0.
    fn check(
        config: &DeviceConfig,
        reading: f64,
        point: Option<Value>,
        range: Option<Value>,
    ) -> Vec<String> {
        let mut alarm = Alarm::new(0.0, 0.0);
        let conv = Conversion::new(config, Params::from_config(config));
        check_alarm_at(
            &mut alarm,
            &conv,
            reading,
            point.as_ref(),
            range.as_ref(),
            "Tank",
            Some(0.0),
        )
        .into_iter()
        .collect()
    }

    #[test]
    fn untouched_slider_does_not_notify() {
        assert!(check(&make_config(enabled(json!({}))), MID_SCALE_MA, None, None).is_empty());
    }

    #[test]
    fn greater_than_message() {
        let c = make_config(enabled(json!({"reading_type": "Level Reading"})));
        assert_eq!(
            check(&c, MID_SCALE_MA, Some(json!(4.0)), None),
            ["Tank has exceeded 4 m with a value of 5 m"]
        );
    }

    #[test]
    fn readings_are_rounded_to_two_decimal_places() {
        let c = make_config(enabled(
            json!({"alarm_type": "Less Than", "reading_type": "Level Reading"}),
        ));
        assert_eq!(
            check(&c, 12.8, Some(json!(6.98765)), None),
            ["Tank has dropped below 6.99 m with a value of 5.5 m"]
        );
    }

    #[test]
    fn less_than_message() {
        let c = make_config(enabled(
            json!({"alarm_type": "Less Than", "reading_type": "Level Reading"}),
        ));
        assert_eq!(
            check(&c, MID_SCALE_MA, Some(json!(6.0)), None),
            ["Tank has dropped below 6 m with a value of 5 m"]
        );
    }

    #[test]
    fn allowed_range_reports_the_crossed_bound() {
        let c = make_config(enabled(
            json!({"alarm_type": "Allowed Range", "reading_type": "Level Reading"}),
        ));
        assert_eq!(
            check(&c, MID_SCALE_MA, None, Some(json!([1.0, 4.0]))),
            ["Tank has exceeded 4 m with a value of 5 m"]
        );
        assert_eq!(
            check(&c, MID_SCALE_MA, None, Some(json!([6.0, 9.0]))),
            ["Tank has dropped below 6 m with a value of 5 m"]
        );
        // reversed [high, low]
        assert_eq!(
            check(&c, MID_SCALE_MA, None, Some(json!([4.0, 1.0]))),
            ["Tank has exceeded 4 m with a value of 5 m"]
        );
        assert!(check(&c, MID_SCALE_MA, None, Some(json!([1.0, 9.0]))).is_empty());
    }

    #[test]
    fn malformed_dual_slider_value_is_silent() {
        let c = make_config(enabled(json!({"alarm_type": "Allowed Range"})));
        assert!(check(&c, MID_SCALE_MA, None, Some(json!(4.0))).is_empty());
    }

    #[test]
    fn disabled_alarm_never_notifies_even_on_a_breach() {
        let c = make_config(enabled(json!({"alarm_enabled": false})));
        assert!(check(&c, MID_SCALE_MA, Some(json!(4.0)), None).is_empty());
    }

    #[test]
    fn legacy_alarm_source_percentage_and_volume() {
        let c = make_config(enabled(
            json!({"alarm_source": "Filled Percentage", "reading_type": null}),
        ));
        assert_eq!(
            check(&c, MID_SCALE_MA, Some(json!(40.0)), None),
            ["Tank has exceeded 40 % with a value of 50 %"]
        );
        let c = make_config(enabled(
            json!({"alarm_source": "Volume", "reading_type": null}),
        ));
        assert_eq!(
            check(&c, MID_SCALE_MA, Some(json!(400.0)), None),
            ["Tank has exceeded 400 L with a value of 500 L"]
        );
    }

    #[test]
    fn specific_reading_type_drives_the_alarm() {
        let c = make_config(enabled(json!({"reading_type": "Filled Percentage"})));
        assert_eq!(
            check(&c, MID_SCALE_MA, Some(json!(40.0)), None),
            ["Tank has exceeded 40 % with a value of 50 %"]
        );
    }

    #[test]
    fn alarm_value_tracks_the_configured_source() {
        for (source, expected) in [
            ("Level Reading", 5.0),
            ("Filled Percentage", 50.0),
            ("Volume", 500.0),
        ] {
            let c = make_config(enabled(
                json!({"alarm_source": source, "reading_type": null}),
            ));
            let conv = Conversion::new(&c, Params::from_config(&c));
            assert_eq!(alarm_value(&conv, MID_SCALE_MA), Some(expected));
        }
    }

    #[test]
    fn config_without_alarm_keys_never_notifies() {
        assert!(check(
            &make_config(json!({})),
            MID_SCALE_MA,
            Some(json!(4.0)),
            None
        )
        .is_empty());
    }

    #[test]
    fn level_alarm_compares_in_the_configured_depth_units() {
        let c = make_config(enabled(
            json!({"reading_type": "Level Reading", "depth_units": "mm"}),
        ));
        assert!(check(&c, MID_SCALE_MA, Some(json!(6000.0)), None).is_empty());
        assert_eq!(
            check(&c, 16.0, Some(json!(6000.0)), None),
            ["Tank has exceeded 6000 mm with a value of 7500 mm"]
        );
    }

    #[test]
    fn negative_level_formats() {
        assert_eq!(format_value(-0.15), "-0.15");
    }
}

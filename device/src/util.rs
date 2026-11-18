//! Python number formatting the app's messages depend on.

use serde_json::Value;

/// Python `round(x, ndigits)`: correctly rounded, half to even on the exact
/// binary value (which is what Rust's `{:.N}` formatting does too).
pub fn round_dp(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.ndigits$}").parse().unwrap_or(x)
}

/// Python `str(float)` / `repr(float)`: shortest round-trip digits, always
/// with a decimal point or exponent.
pub fn py_float(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let abs = x.abs();
    if abs != 0.0 && !(1e-4..1e16).contains(&abs) {
        // Python switches to exponent notation outside this range.
        let s = format!("{x:e}");
        let (mantissa, exp) = s.split_once('e').unwrap();
        let exp: i32 = exp.parse().unwrap();
        let sign = if exp < 0 { '-' } else { '+' };
        return format!("{mantissa}e{sign}{:02}", exp.abs());
    }
    let s = format!("{x}");
    if s.contains('.') {
        s
    } else {
        format!("{s}.0")
    }
}

/// Python `f"{x:g}"`: 6 significant digits, trailing zeros stripped,
/// exponent notation below 1e-4 or from 1e6.
pub fn format_g(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0".into() } else { "0".into() };
    }
    // Round to 6 significant digits first; the exponent is the rounded one's.
    let sci = format!("{x:.5e}");
    let (mantissa, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    if (-4..6).contains(&exp) {
        let decimals = (5 - exp).max(0) as usize;
        strip_zeros(&format!("{x:.decimals$}"))
    } else {
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{}e{sign}{:02}", strip_zeros(mantissa), exp.abs())
    }
}

fn strip_zeros(s: &str) -> String {
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s.to_string()
    }
}

/// Python `repr()` of a JSON value, as an RPC's request arrives (for error
/// messages that quote what was sent).
pub fn py_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match n.as_i64().or_else(|| n.as_u64().map(|u| u as i64)) {
            Some(i) if !n.is_f64() => i.to_string(),
            _ => py_float(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
        Value::Array(items) => {
            format!("[{}]", items.iter().map(py_repr).collect::<Vec<_>>().join(", "))
        }
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("'{k}': {}", py_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// Python `json.dumps(value)` (default `ensure_ascii=True`): non-ASCII
/// characters escaped as `\uXXXX`, which is how pydoover writes a string
/// default into a `$tag` reference.
pub fn json_dumps_ascii(v: &Value) -> String {
    let compact = serde_json::to_string(v).expect("JSON value serializes");
    let mut out = String::with_capacity(compact.len());
    for c in compact.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut buf = [0u16; 2];
            for unit in c.encode_utf16(&mut buf) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn g_formatting_matches_python() {
        assert_eq!(format_g(4.0), "4");
        assert_eq!(format_g(5.5), "5.5");
        assert_eq!(format_g(6.99), "6.99");
        assert_eq!(format_g(-0.15), "-0.15");
        assert_eq!(format_g(7500.0), "7500");
        assert_eq!(format_g(100000.0), "100000");
        assert_eq!(format_g(1234567.0), "1.23457e+06");
        assert_eq!(format_g(0.00001), "1e-05");
        assert_eq!(format_g(-100.0), "-100");
        assert_eq!(format_g(0.0001), "0.0001");
    }

    #[test]
    fn rounding_matches_python() {
        assert_eq!(round_dp(6.98765, 2), 6.99);
        assert_eq!(round_dp(0.123456, 4), 0.1235);
        assert_eq!(round_dp(2.675, 2), 2.67); // binary 2.67499…
        assert_eq!(round_dp(-0.00004, 4) + 0.0, 0.0);
    }

    #[test]
    fn python_repr() {
        assert_eq!(py_repr(&json!("abc")), "'abc'");
        assert_eq!(py_repr(&Value::Null), "None");
        assert_eq!(py_repr(&json!(true)), "True");
        assert_eq!(py_repr(&json!({"value": 1000})), "{'value': 1000}");
        assert_eq!(py_repr(&json!(100.01)), "100.01");
        assert_eq!(py_repr(&json!(4.0)), "4.0");
        assert_eq!(py_float(0.5), "0.5");
        assert_eq!(py_float(4.0), "4.0");
    }

    #[test]
    fn ascii_json() {
        assert_eq!(json_dumps_ascii(&json!("a — b")), "\"a \\u2014 b\"");
    }
}

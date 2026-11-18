//! The device app's tags — `common/common_tags.py` +
//! `analog_level_sensor/app_tags.py`.
//!
//! The declared [`DeviceTags`] give the runtime its live-tag list and the UI
//! its `$tag` references. Writes go through a [`TagIo`] by name instead of the
//! typed handles: several tags are set to `null` (a fault clears the level,
//! a disabled calibration clears its readbacks), which a typed `Tag<f64>`
//! cannot express, and it keeps the conversion testable without a runtime.

use doover::tags::Tag;
use doover::Tags;
use serde_json::Value;

/// `sensor_fault` while the input is in fault; more codes may follow.
pub const SENSOR_FAULT_UNDER_RANGE: &str = "under_range";
pub const SENSOR_FAULT_DEFAULT_MESSAGE: &str =
    "Sensor signal below range — check the sensor and its wiring";

pub const LEVEL_FILLED_PERCENTAGE: &str = "level_filled_percentage";
pub const LEVEL_READING: &str = "level_reading";
pub const LEVEL_READING_DISPLAY: &str = "level_reading_display";
pub const RAW_LEVEL_READING: &str = "raw_level_reading";
pub const LEVEL_VOLUME: &str = "level_volume";
pub const SENSOR_FAULT: &str = "sensor_fault";
pub const SENSOR_FAULT_HIDDEN: &str = "sensor_fault_hidden";
pub const SENSOR_FAULT_MESSAGE: &str = "sensor_fault_message";
pub const OPERATOR_CALIBRATION: &str = "operator_calibration";

// Most handles are only read for their declaration (live list, UI refs).
#[allow(dead_code)]
#[derive(Tags)]
pub struct DeviceTags {
    #[tag(live, default = None)]
    pub level_filled_percentage: Tag<f64>,
    #[tag(live, default = None)]
    pub level_reading: Tag<f64>,
    /// level_reading converted to the configured depth units, for this app's
    /// own gauge; peers keep reading level_reading (metres).
    #[tag(live, default = None)]
    pub level_reading_display: Tag<f64>,
    #[tag(default = None)]
    pub raw_level_reading: Tag<f64>,
    #[tag(default = None)]
    pub level_volume: Tag<f64>,
    /// Peer contract (sia-local-control HMI, sia-injection-controller): a code
    /// while the input is in fault, None otherwise. While it is set the level,
    /// percentage, display and volume tags are None.
    #[tag(live, default = None)]
    pub sensor_fault: Tag<String>,
    /// Drive this app's own warning indicator; the UI schema is published
    /// once, so its visibility and text are bound to tags.
    #[tag(default = true)]
    pub sensor_fault_hidden: Tag<bool>,
    #[tag(default = SENSOR_FAULT_DEFAULT_MESSAGE)]
    pub sensor_fault_message: Tag<String>,
    // Operator Sensor Calibration readbacks: the values the conversion is
    // using now. Only published while the feature is enabled; the HMI locks
    // its Sensor cells unless operator_calibration is true.
    #[tag(live, default = None)]
    pub zero_m: Tag<f64>,
    #[tag(live, default = None)]
    pub span_m: Tag<f64>,
    #[tag(live, default = None)]
    pub fluid_density: Tag<f64>,
    #[tag(live, default = None)]
    pub operator_calibration: Tag<bool>,
}

/// Tag reads and (buffered) writes by name, scoped to this app.
pub trait TagIo: Send {
    /// The current value (pending writes included); `None` when unset.
    fn get(&self, name: &str) -> Option<Value>;
    fn set(&mut self, name: &str, value: Value);
}

/// Writes collected over one step, then handed to the runtime in one
/// `set_tags` (one aggregate merge rather than one per tag).
pub struct PendingTags<'a> {
    ctx: &'a doover::AppContext,
    pub writes: Vec<(String, Value)>,
}

impl<'a> PendingTags<'a> {
    pub fn new(ctx: &'a doover::AppContext) -> Self {
        Self { ctx, writes: Vec::new() }
    }

    pub async fn flush(self) -> doover::Result<()> {
        if self.writes.is_empty() {
            return Ok(());
        }
        self.ctx.set_tags(self.writes).await
    }
}

impl TagIo for PendingTags<'_> {
    fn get(&self, name: &str) -> Option<Value> {
        if let Some((_, v)) = self.writes.iter().rev().find(|(n, _)| n == name) {
            return Some(v.clone()).filter(|v| !v.is_null());
        }
        self.ctx.get_tag(name).filter(|v| !v.is_null())
    }

    fn set(&mut self, name: &str, value: Value) {
        self.writes.retain(|(n, _)| n != name);
        self.writes.push((name.to_string(), value));
    }
}

/// An in-memory [`TagIo`] for tests.
#[cfg(test)]
#[derive(Debug, Default, Clone)]
pub struct MemoryTags {
    pub store: std::collections::HashMap<String, Value>,
    pub writes: Vec<(String, Value)>,
}

#[cfg(test)]
impl MemoryTags {
    pub fn value(&self, name: &str) -> Value {
        self.store.get(name).cloned().unwrap_or(Value::Null)
    }
}

#[cfg(test)]
impl TagIo for MemoryTags {
    fn get(&self, name: &str) -> Option<Value> {
        self.store.get(name).cloned().filter(|v| !v.is_null())
    }

    fn set(&mut self, name: &str, value: Value) {
        self.writes.push((name.to_string(), value.clone()));
        self.store.insert(name.to_string(), value);
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use doover::tags::TagsCollection;

    pub type FakeTags = MemoryTags;

    #[test]
    fn live_tags_match_the_python_declarations() {
        let mut live = DeviceTags::live_tag_names();
        live.sort();
        assert_eq!(
            live,
            vec![
                "fluid_density",
                "level_filled_percentage",
                "level_reading",
                "level_reading_display",
                "operator_calibration",
                "sensor_fault",
                "span_m",
                "zero_m",
            ]
        );
    }

    #[test]
    fn fault_tag_defaults() {
        let tags = DeviceTags::detached();
        assert_eq!(tags.sensor_fault_hidden.get(), Some(true));
        assert_eq!(tags.sensor_fault_message.get().as_deref(), Some(SENSOR_FAULT_DEFAULT_MESSAGE));
        assert_eq!(tags.sensor_fault.get(), None);
    }
}

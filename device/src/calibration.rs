//! Operator Sensor Calibration — `analog_level_sensor/calibration.py`.
//!
//! Operators tune the 4-20 mA conversion on site, from this app's cloud UI and
//! the local HMI, without a redeploy:
//!
//! | Value / RPC / tag | Meaning                     | Range                  | Default without an operator value |
//! |-------------------|-----------------------------|------------------------|-----------------------------------|
//! | `zero_m`          | minimum level (m)           | -100 <= z < span       | config `sensor_minimum_metres`    |
//! | `span_m`          | maximum level (m)           | 0 < s <= 100, s > zero | config `sensor_maximum_metres`    |
//! | `fluid_density`   | kg/m³ (submersible scaling) | 500..2500              | config `fluid_density` (1000)     |
//!
//! Always metres. The zero may be negative (the minimum-level end below the
//! tank datum); the span stays above 0 m. Everything is gated on the
//! `operator_calibration_enabled` config field (default off): off, the
//! submodule is hidden, the RPCs are refused with `UNAVAILABLE`, the readback
//! tags are never published and the conversion uses the config.
//!
//! On: the value in effect is the operator value when a valid one is set,
//! else the config default, read on every reading. Each value is stored in
//! ui_cmds under its element name, so it survives a restart and the cloud
//! input shows an HMI change. "Reset to configured values" writes each config
//! default into ui_cmds with a `<name>_default` marker holding the same
//! number: a ui_cmds value equal to its marker is "no operator value", so a
//! later config change still takes effect (and is written back).
//!
//! See the Python module's docstrings for the echo / late-sync / offline-boot
//! reasoning; the state machine here is a line-for-line port of it.

use std::time::Instant;

use serde_json::{json, Map, Value};

use crate::config::DeviceConfig;
use crate::conversion::Params;
use crate::tags::{self, TagIo};
use crate::util::{format_g, py_repr, round_dp};
use doover::rpc::RpcError;

pub const RESET_ELEMENT: &str = "reset_calibration";
pub const UNAVAILABLE_MESSAGE: &str = "Enable Operator Sensor Calibration on the sensor app";

/// Bounded wait for the ui_cmds aggregate at startup.
pub const UI_CMDS_SYNC_TIMEOUT_SECS: u64 = 10;

pub const DECIMALS: usize = 4;

/// An echo not seen within this long is forgotten.
pub const ECHO_TIMEOUT_SECS: f64 = 60.0;

/// `value` as a finite float (pydoover `float(value)`), or `None`.
fn finite(value: Option<&Value>) -> Option<f64> {
    let number = match value? {
        Value::Number(n) => n.as_f64()?,
        Value::String(s) => s.trim().parse::<f64>().ok()?,
        _ => return None, // None, bools, containers
    };
    number.is_finite().then_some(number)
}

/// Rounded to 4 dp; `+ 0.0` turns -0.0 into 0.0.
fn round4(number: f64) -> f64 {
    round_dp(number, DECIMALS) + 0.0
}

/// `value` as a finite float rounded to 4 dp, or `None`.
fn number(value: Option<&Value>) -> Option<f64> {
    finite(value).map(round4)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Zero,
    Span,
    Density,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CalibrationValue {
    /// UI element, RPC method, ui_cmds key and readback tag.
    pub name: &'static str,
    /// The `<name>_default` ui_cmds marker.
    pub marker: &'static str,
    /// Operator text for logs and errors.
    pub label: &'static str,
    pub units: &'static str,
    pub minimum: f64,
    pub maximum: f64,
    pub min_inclusive: bool,
    pub max_inclusive: bool,
    pub kind: Kind,
}

pub const ZERO: CalibrationValue = CalibrationValue {
    name: "zero_m",
    marker: "zero_m_default",
    label: "zero",
    units: "m",
    minimum: -100.0,
    maximum: 100.0,
    min_inclusive: true,
    max_inclusive: false,
    kind: Kind::Zero,
};
pub const SPAN: CalibrationValue = CalibrationValue {
    name: "span_m",
    marker: "span_m_default",
    label: "span",
    units: "m",
    minimum: 0.0,
    maximum: 100.0,
    min_inclusive: false,
    max_inclusive: true,
    kind: Kind::Span,
};
pub const DENSITY: CalibrationValue = CalibrationValue {
    name: "fluid_density",
    marker: "fluid_density_default",
    label: "fluid density",
    units: "kg/m³",
    minimum: 500.0,
    maximum: 2500.0,
    min_inclusive: true,
    max_inclusive: true,
    kind: Kind::Density,
};

pub const VALUES: [CalibrationValue; 3] = [ZERO, SPAN, DENSITY];

pub fn by_name(name: &str) -> Option<(usize, CalibrationValue)> {
    VALUES.iter().copied().enumerate().find(|(_, v)| v.name == name)
}

impl CalibrationValue {
    /// `value` as a float within range (rounded to 4 dp), or `None`. Both the
    /// value as given and the rounded one must be in range.
    pub fn parse(&self, value: Option<&Value>) -> Option<f64> {
        let number = finite(value)?;
        let rounded = round4(number);
        (self.in_range(number) && self.in_range(rounded)).then_some(rounded)
    }

    fn in_range(&self, n: f64) -> bool {
        let above = if self.min_inclusive { n >= self.minimum } else { n > self.minimum };
        let below = if self.max_inclusive { n <= self.maximum } else { n < self.maximum };
        above && below
    }

    /// The value without an operator value, from the deployment config.
    pub fn config_default(&self, config: &CalibrationConfig) -> Option<f64> {
        let v = match self.kind {
            Kind::Zero => config.zero_m,
            Kind::Span => config.span_m,
            Kind::Density => config.fluid_density,
        };
        v.is_finite().then_some(v)
    }

    pub fn describe_range(&self) -> String {
        let low = if self.min_inclusive { "" } else { "above " };
        let high = if self.max_inclusive { "" } else { "below " };
        let units = if self.units.is_empty() { String::new() } else { format!(" {}", self.units) };
        format!("{low}{} to {high}{}{units}", format_g(self.minimum), format_g(self.maximum))
    }
}

/// The deployment config this feature reads (a snapshot, so the RPC handlers
/// need not share the whole config).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CalibrationConfig {
    pub enabled: bool,
    pub zero_m: f64,
    pub span_m: f64,
    pub fluid_density: f64,
}

impl CalibrationConfig {
    pub fn from_config(config: &DeviceConfig) -> Self {
        Self {
            enabled: config.raw.operator_calibration_enabled,
            zero_m: config.raw.sensor_minimum_metres,
            span_m: config.raw.sensor_maximum_metres,
            fluid_density: config.config_fluid_density(),
        }
    }
}

/// The values the conversion uses now.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Effective {
    pub zero_m: Option<f64>,
    pub span_m: Option<f64>,
    pub fluid_density: Option<f64>,
}

impl Effective {
    /// As conversion [`Params`], with the config filling anything missing.
    pub fn params(&self, fallback: Params) -> Params {
        Params {
            zero_m: self.zero_m.unwrap_or(fallback.zero_m),
            span_m: self.span_m.unwrap_or(fallback.span_m),
            fluid_density: self.fluid_density.unwrap_or(fallback.fluid_density),
        }
    }

    pub fn to_json(self) -> Value {
        json!({"zero_m": self.zero_m, "span_m": self.span_m, "fluid_density": self.fluid_density})
    }
}

/// (value, marker) as read from ui_cmds, each a 4 dp float or `None`.
type Observed = (Option<f64>, Option<f64>);

/// What [`OperatorValue`] reads its surroundings through.
pub struct Env {
    pub config: CalibrationConfig,
    /// This app's ui_cmds subtree (pydoover `ui_manager.values`).
    pub ui_values: Map<String, Value>,
    pub ui_cmds_synced: bool,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
}

impl Env {
    fn now(&self) -> f64 {
        (self.clock)()
    }

    fn delivered(&self) -> bool {
        !self.ui_values.is_empty()
    }
}

/// One calibration value's operator setting on the running app (pydoover
/// `OperatorValue`; see its docstring for the echo bookkeeping).
#[derive(Debug, Clone, PartialEq)]
pub struct OperatorValue {
    pub spec: CalibrationValue,
    /// In effect (`None` = no operator value; the config default applies).
    value: Option<f64>,
    /// The ui_cmds (value, marker) last seen.
    seen: Option<Observed>,
    /// This app's writes not yet echoed back, oldest first.
    pending: Vec<(Observed, f64)>,
    unsynced_write: bool,
    repersist: bool,
    /// Restored from the readback tag at an offline boot.
    provisional: bool,
}

/// The state a write changes, for rollback if it fails.
pub type Snapshot = OperatorValue;

impl OperatorValue {
    fn new(spec: CalibrationValue) -> Self {
        Self {
            spec,
            value: None,
            seen: None,
            pending: Vec::new(),
            unsynced_write: false,
            repersist: false,
            provisional: false,
        }
    }

    pub fn default(&self, env: &Env) -> Option<f64> {
        self.spec.config_default(&env.config)
    }

    fn observed(&self, env: &Env) -> Option<Observed> {
        if !env.ui_values.contains_key(self.spec.name) {
            return None;
        }
        Some((
            self.spec.parse(env.ui_values.get(self.spec.name)),
            number(env.ui_values.get(self.spec.marker)),
        ))
    }

    fn operator_value((value, marker): Observed) -> Option<f64> {
        if value == marker {
            None
        } else {
            value
        }
    }

    /// The operator value in effect, or `None` when none is set.
    pub fn setting(&mut self, env: &Env) -> Option<f64> {
        let observed = self.observed(env);
        if observed.is_none() && self.provisional && env.delivered() {
            // ui_cmds has synced and holds no value: the restored one was the
            // default of an older config.
            self.provisional = false;
            self.value = None;
        }
        if !self.pending.is_empty() {
            let expired = env.now() - ECHO_TIMEOUT_SECS;
            self.pending.retain(|(_, t)| *t >= expired);
        }
        if observed != self.seen {
            self.seen = observed;
            if let Some(obs) = observed.filter(|o| o.0.is_some()) {
                let stored = Self::operator_value(obs);
                let echoed = self.pending.iter().position(|(o, _)| *o == obs);
                if let Some(i) = echoed {
                    // Our own write; a newer one still on its way wins.
                    self.pending.drain(..=i);
                    if self.pending.is_empty() {
                        self.value = stored;
                    }
                } else if self.unsynced_write && stored != self.value {
                    self.repersist = true;
                } else {
                    self.value = stored;
                }
                self.unsynced_write = false;
                self.provisional = false;
            }
        }
        self.value
    }

    /// The operator value, else the config default.
    pub fn value(&mut self, env: &Env) -> Option<f64> {
        self.setting(env).or_else(|| self.default(env))
    }

    fn check(&self, value: &Value) -> Result<f64, RpcError> {
        self.spec.parse(Some(value)).ok_or_else(|| {
            RpcError::new(
                "INVALID",
                format!(
                    "the {} must be {}, got {}",
                    self.spec.label,
                    self.spec.describe_range(),
                    py_repr(value)
                ),
            )
        })
    }

    /// Start-up: if ui_cmds did not sync, carry over the value in effect
    /// before the reboot from its readback tag (unless it is the default).
    fn restore(&mut self, env: &Env, persisted: Option<&Value>) {
        if env.ui_cmds_synced || self.observed(env).is_some() {
            return;
        }
        let Some(number) = self.spec.parse(persisted) else { return };
        if let Some(default) = self.default(env) {
            if number == round_dp(default, DECIMALS) {
                return;
            }
        }
        self.value = Some(number);
        self.provisional = true;
        tracing::warn!(
            "ui_cmds not synced: using the last {} in effect, {} {}, from the {} tag until it does",
            self.spec.label,
            crate::util::py_float(number),
            self.spec.units,
            self.spec.name,
        );
    }

    fn begin_write(&mut self, env: &Env) {
        // Take in the ui_cmds value first, so one not yet seen is not adopted
        // over this write on the next read.
        self.setting(env);
        if self.observed(env).is_none() {
            self.unsynced_write = true;
        }
        self.provisional = false;
    }

    /// The ui_cmds keys for the value in effect, expecting their echo.
    fn payload(&mut self, env: &Env) -> Map<String, Value> {
        let observed: Observed = match self.value {
            Some(v) => (Some(v), None),
            None => {
                let number = self.default(env).map(|d| round_dp(d, DECIMALS));
                (number, number)
            }
        };
        let last = self.pending.last().map(|p| Some(p.0)).unwrap_or(self.seen);
        if observed.0.is_some() && Some(observed) != last {
            self.pending.push((observed, env.now()));
        }
        let mut m = Map::new();
        m.insert(self.spec.name.into(), json!(observed.0));
        m.insert(self.spec.marker.into(), json!(observed.1));
        m
    }

    fn set_payload(&mut self, env: &Env, number: f64) -> Map<String, Value> {
        self.begin_write(env);
        self.value = Some(number);
        self.payload(env)
    }

    fn reset_payload(&mut self, env: &Env) -> Map<String, Value> {
        self.begin_write(env);
        self.value = None;
        self.payload(env)
    }

    /// ui_cmds keys to write again, if any: a late sync delivered an older
    /// value over a local write, or a reset value is no longer the config
    /// default.
    fn maintenance_payload(&mut self, env: &Env) -> Option<Map<String, Value>> {
        self.setting(env);
        if self.repersist {
            self.repersist = false;
            tracing::info!("ui_cmds synced an older {}; keeping {:?}", self.spec.label, self.value);
            return Some(self.payload(env));
        }
        if let Some((Some(value), marker)) = self.seen {
            if self.value.is_none() && self.pending.is_empty() && Some(value) == marker {
                if let Some(default) = self.default(env) {
                    if round_dp(default, DECIMALS) != value {
                        return Some(self.payload(env));
                    }
                }
            }
        }
        None
    }
}

/// Writes one ui_cmds aggregate update (`{app_key: payload}`).
#[doover::async_trait]
pub trait UiCmdsWriter: Send {
    async fn write(&mut self, payload: Map<String, Value>) -> doover::Result<()>;
}

/// The three operator values, their RPCs and readback tags.
pub struct SensorCalibration {
    pub values: [OperatorValue; 3],
    pub env: Env,
    inverted_logged: Option<(f64, f64)>,
}

impl SensorCalibration {
    pub fn new(config: CalibrationConfig) -> Self {
        let epoch = Instant::now();
        Self::with_clock(config, Box::new(move || epoch.elapsed().as_secs_f64()))
    }

    pub fn with_clock(config: CalibrationConfig, clock: Box<dyn Fn() -> f64 + Send + Sync>) -> Self {
        Self {
            values: VALUES.map(OperatorValue::new),
            env: Env { config, ui_values: Map::new(), ui_cmds_synced: false, clock },
            inverted_logged: None,
        }
    }

    pub fn enabled(&self) -> bool {
        self.env.config.enabled
    }

    /// The setting (operator value) for `name`, for inspection.
    #[cfg(test)]
    pub fn setting(&mut self, name: &str) -> Option<f64> {
        let (i, _) = by_name(name)?;
        self.values[i].setting(&self.env)
    }

    /// The values the conversion uses now.
    pub fn effective(&mut self) -> Effective {
        let env = &self.env;
        let [zero, span, density] = &mut self.values;
        let mut zero_m = zero.value(env);
        let mut span_m = span.value(env);
        if let (Some(z), Some(s)) = (zero_m, span_m) {
            if z >= s && (zero.setting(env).is_some() || span.setting(env).is_some()) {
                // Only reachable by a direct ui_cmds edit or a config change
                // under an operator value (the RPCs refuse it): an inverted
                // range would read backwards, so use the config for both.
                if self.inverted_logged != Some((z, s)) {
                    self.inverted_logged = Some((z, s));
                    tracing::warn!(
                        "Operator zero {} m is not below span {} m; using the configured zero and span",
                        crate::util::py_float(z),
                        crate::util::py_float(s),
                    );
                }
                zero_m = zero.default(env);
                span_m = span.default(env);
            }
        }
        Effective { zero_m, span_m, fluid_density: density.value(env) }
    }

    /// Before anything publishes. Off: clear readback tags a previous enabled
    /// run left behind. On: restore from the tags if ui_cmds did not sync
    /// (`synced`, from a bounded wait), then publish.
    pub fn startup(&mut self, synced: bool, tags: &mut dyn TagIo) {
        if !self.enabled() {
            for name in [ZERO.name, SPAN.name, DENSITY.name, tags::OPERATOR_CALIBRATION] {
                if tags.get(name).is_some() {
                    tags.set(name, Value::Null);
                }
            }
            return;
        }
        self.env.ui_cmds_synced = synced;
        if !synced {
            tracing::warn!(
                "ui_cmds did not sync within {UI_CMDS_SYNC_TIMEOUT_SECS}s; sensor calibration \
                 from the readback tags until it does"
            );
        }
        for i in 0..self.values.len() {
            let persisted = tags.get(self.values[i].spec.name);
            self.values[i].restore(&self.env, persisted.as_ref());
        }
        self.publish(tags);
    }

    /// Every loop while enabled: rewrite ui_cmds where needed and publish the
    /// readback tags.
    pub async fn maintain(&mut self, tags: &mut dyn TagIo, writer: &mut dyn UiCmdsWriter) {
        if !self.enabled() {
            return;
        }
        let mut payload = Map::new();
        for value in &mut self.values {
            if let Some(keys) = value.maintenance_payload(&self.env) {
                payload.extend(keys);
            }
        }
        if !payload.is_empty() {
            if let Err(e) = writer.write(payload).await {
                // A UI write must not stop the loop.
                tracing::warn!("Could not store the sensor calibration: {e}");
            }
        }
        self.publish(tags);
    }

    pub fn publish(&mut self, tags: &mut dyn TagIo) {
        let effective = self.effective();
        tags.set(ZERO.name, json!(effective.zero_m));
        tags.set(SPAN.name, json!(effective.span_m));
        tags.set(DENSITY.name, json!(effective.fluid_density));
        tags.set(tags::OPERATOR_CALIBRATION, Value::Bool(true));
    }

    fn require_enabled(&self) -> Result<(), RpcError> {
        if self.enabled() {
            Ok(())
        } else {
            Err(RpcError::new("UNAVAILABLE", UNAVAILABLE_MESSAGE))
        }
    }

    /// Set one value. Out of range, or a zero not below the span: `INVALID`,
    /// and the value in effect is unchanged.
    pub async fn request(
        &mut self,
        name: &str,
        value: &Value,
        tags: &mut dyn TagIo,
        writer: &mut dyn UiCmdsWriter,
    ) -> Result<f64, RpcError> {
        self.require_enabled()?;
        let (i, spec) = by_name(name)
            .ok_or_else(|| RpcError::new("INVALID", format!("unknown calibration value {name:?}")))?;
        let number = self.values[i].check(value)?;
        if spec.kind != Kind::Density {
            let current = self.effective();
            let zero = if spec.kind == Kind::Zero { Some(number) } else { current.zero_m };
            let span = if spec.kind == Kind::Span { Some(number) } else { current.span_m };
            if let (Some(z), Some(s)) = (zero, span) {
                #[allow(clippy::neg_cmp_op_on_partial_ord)] // Python `not zero < span`
                if !(z < s) {
                    return Err(RpcError::new(
                        "INVALID",
                        format!("the zero ({} m) must be below the span ({} m)", format_g(z), format_g(s)),
                    ));
                }
            }
        }
        let saved = vec![(i, self.values[i].clone())];
        let payload = self.values[i].set_payload(&self.env, number);
        self.commit(payload, saved, writer).await?;
        self.publish(tags);
        tracing::info!(
            "Sensor {} set to {} {}",
            spec.label,
            crate::util::py_float(number),
            spec.units
        );
        Ok(number)
    }

    /// Clear every operator value: back to the config defaults.
    pub async fn reset(
        &mut self,
        tags: &mut dyn TagIo,
        writer: &mut dyn UiCmdsWriter,
    ) -> Result<Effective, RpcError> {
        self.require_enabled()?;
        let saved: Vec<(usize, Snapshot)> = self.values.iter().cloned().enumerate().collect();
        let mut payload = Map::new();
        for value in &mut self.values {
            payload.extend(value.reset_payload(&self.env));
        }
        self.commit(payload, saved, writer).await?;
        self.publish(tags);
        tracing::info!("Sensor calibration reset to the configured values");
        Ok(self.effective())
    }

    /// Store an RPC's change. If ui_cmds cannot be written the change is
    /// undone and the RPC fails, so nothing keeps a value that was not stored.
    async fn commit(
        &mut self,
        payload: Map<String, Value>,
        saved: Vec<(usize, Snapshot)>,
        writer: &mut dyn UiCmdsWriter,
    ) -> Result<(), RpcError> {
        if let Err(e) = writer.write(payload).await {
            for (i, state) in saved {
                self.values[i] = state;
            }
            tracing::warn!("Could not store the sensor calibration: {e}");
            return Err(RpcError::new(
                "UNAVAILABLE",
                "could not store the sensor calibration, try again",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::config::tests::make_config;
    use crate::tags::MemoryTags;
    use std::sync::{Arc, Mutex};

    /// The ui_cmds writer of the Python `Harness`: writes are recorded and
    /// only reach ui_cmds when the test delivers their echo.
    #[derive(Default, Clone)]
    pub struct Writer {
        pub online: bool,
        pub written: Vec<Map<String, Value>>,
        pub aggregate_writes: Vec<Map<String, Value>>,
    }

    #[doover::async_trait]
    impl UiCmdsWriter for Writer {
        async fn write(&mut self, payload: Map<String, Value>) -> doover::Result<()> {
            if !self.online {
                return Err(doover::DooverError::Other("DDA down".into()));
            }
            self.aggregate_writes.push(payload.clone());
            self.written.push(payload);
            Ok(())
        }
    }

    /// The calibration on its own, with the Python harness's stand-ins.
    pub struct Harness {
        pub config: DeviceConfig,
        pub cal: SensorCalibration,
        pub tags: MemoryTags,
        pub writer: Writer,
        pub clock: Arc<Mutex<f64>>,
    }

    pub fn run<F: std::future::Future>(f: F) -> F::Output {
        tokio::runtime::Builder::new_current_thread().build().unwrap().block_on(f)
    }

    impl Harness {
        pub fn new(config: DeviceConfig, values: Value, persisted: Value) -> Self {
            let clock = Arc::new(Mutex::new(1000.0));
            let c = clock.clone();
            let mut cal = SensorCalibration::with_clock(
                CalibrationConfig::from_config(&config),
                Box::new(move || *c.lock().unwrap()),
            );
            cal.env.ui_values = values.as_object().cloned().unwrap_or_default();
            let mut tags = MemoryTags::default();
            for (k, v) in persisted.as_object().cloned().unwrap_or_default() {
                tags.store.insert(k, v);
            }
            Self { config, cal, tags, writer: Writer { online: true, ..Default::default() }, clock }
        }

        pub fn started(config: DeviceConfig, values: Value, synced: bool, persisted: Value) -> Self {
            let mut h = Self::new(config, values, persisted);
            h.cal.startup(synced, &mut h.tags);
            h
        }

        pub fn on(overrides: Value) -> Self {
            let mut o = json!({"operator_calibration_enabled": true});
            for (k, v) in overrides.as_object().cloned().unwrap_or_default() {
                o[k] = v;
            }
            Self::started(make_config(o), json!({}), true, json!({}))
        }

        pub fn request(&mut self, name: &str, value: Value) -> Result<f64, RpcError> {
            run(self.cal.request(name, &value, &mut self.tags, &mut self.writer))
        }

        pub fn reset(&mut self) -> Result<Effective, RpcError> {
            run(self.cal.reset(&mut self.tags, &mut self.writer))
        }

        pub fn maintain(&mut self) {
            run(self.cal.maintain(&mut self.tags, &mut self.writer))
        }

        /// Deliver the next `count` writes (all by default) into ui_cmds: a
        /// deep merge where null deletes a key.
        pub fn echo(&mut self, count: Option<usize>) {
            let count = count.unwrap_or(self.writer.written.len());
            for payload in self.writer.written.drain(..count) {
                for (k, v) in payload {
                    if v.is_null() {
                        self.cal.env.ui_values.remove(&k);
                    } else {
                        self.cal.env.ui_values.insert(k, v);
                    }
                }
            }
        }

        pub fn ui_set(&mut self, key: &str, value: Value) {
            self.cal.env.ui_values.insert(key.into(), value);
        }

        pub fn eff(&mut self) -> (Option<f64>, Option<f64>, Option<f64>) {
            let e = self.cal.effective();
            (e.zero_m, e.span_m, e.fluid_density)
        }

        pub fn params(&mut self) -> Params {
            let fallback = Params::from_config(&self.config);
            self.cal.effective().params(fallback)
        }
    }

    fn s(v: f64) -> Option<f64> {
        Some(v)
    }

    #[test]
    fn off_publishes_no_calibration_tags_and_writes_nothing() {
        let mut h = Harness::started(make_config(json!({})), json!({}), true, json!({}));
        h.maintain();
        assert!(h.tags.writes.is_empty());
        assert!(h.writer.aggregate_writes.is_empty());
    }

    #[test]
    fn off_clears_tags_left_by_an_earlier_enabled_run() {
        let persisted = json!({"zero_m": 0.5, "span_m": 4.0, "fluid_density": 1100.0, "operator_calibration": true});
        let h = Harness::started(
            make_config(json!({"operator_calibration_enabled": false})),
            json!({}),
            true,
            persisted,
        );
        for n in ["zero_m", "span_m", "fluid_density", "operator_calibration"] {
            assert_eq!(h.tags.value(n), Value::Null);
        }
    }

    #[test]
    fn off_refuses_the_rpcs() {
        for method in ["zero_m", "span_m", "fluid_density"] {
            let mut h = Harness::started(make_config(json!({})), json!({}), true, json!({}));
            assert_eq!(h.request(method, json!(1.0)).unwrap_err().code, "UNAVAILABLE");
            assert_eq!(h.reset().unwrap_err().code, "UNAVAILABLE");
            assert!(h.writer.aggregate_writes.is_empty());
        }
    }

    #[test]
    fn on_without_operator_values_publishes_the_config() {
        let h = Harness::on(json!({"fluid_density": 1250.0}));
        assert_eq!(h.tags.value("zero_m"), json!(0.0));
        assert_eq!(h.tags.value("span_m"), json!(10.0));
        assert_eq!(h.tags.value("fluid_density"), json!(1250.0));
        assert_eq!(h.tags.value("operator_calibration"), json!(true));
    }

    #[test]
    fn invalid_values_are_refused_and_leave_the_value() {
        for (method, value) in [
            ("zero_m", json!(-100.01)),
            ("zero_m", json!(-100.00004)),
            ("zero_m", json!(10.0)),
            ("zero_m", json!(100.0)),
            ("span_m", json!(0.0)),
            ("span_m", json!(-0.5)),
            ("span_m", json!(100.01)),
            ("span_m", json!(100.00004)),
            ("span_m", json!(0.00004)),
            ("fluid_density", json!(499.9)),
            ("fluid_density", json!(2500.1)),
            ("zero_m", json!("abc")),
            ("span_m", Value::Null),
            ("span_m", json!(true)),
            ("fluid_density", json!({"value": 1000})),
            ("zero_m", json!("nan")),
            ("span_m", json!("inf")),
        ] {
            let mut h = Harness::on(json!({}));
            let before = h.eff();
            let err = h.request(method, value.clone()).unwrap_err();
            assert_eq!(err.code, "INVALID", "{method} {value}");
            assert_eq!(h.eff(), before);
            assert!(h.writer.aggregate_writes.is_empty());
        }
    }

    #[test]
    fn invalid_message_quotes_the_value() {
        let mut h = Harness::on(json!({}));
        let err = h.request("zero_m", json!("abc")).unwrap_err();
        assert_eq!(err.message, "the zero must be -100 to below 100 m, got 'abc'");
    }

    #[test]
    fn span_must_stay_above_an_operator_zero() {
        let mut h = Harness::on(json!({}));
        h.request("zero_m", json!(3.0)).unwrap();
        assert_eq!(h.request("span_m", json!(3.0)).unwrap_err().code, "INVALID");
        assert_eq!(h.eff().1, s(10.0));
    }

    #[test]
    fn boundaries_and_rounding() {
        let mut h = Harness::on(json!({}));
        assert_eq!(h.request("span_m", json!(100)).unwrap(), 100.0);
        assert_eq!(h.request("zero_m", json!(0)).unwrap(), 0.0);
        assert_eq!(h.request("fluid_density", json!(500)).unwrap(), 500.0);
        assert_eq!(h.request("fluid_density", json!(2500)).unwrap(), 2500.0);
        assert_eq!(h.request("zero_m", json!("0.123456")).unwrap(), 0.1235);
        assert_eq!(h.request("span_m", json!(7.25)).unwrap(), 7.25);
        let zero = h.request("zero_m", json!(-0.0)).unwrap();
        assert!(zero == 0.0 && zero.is_sign_positive());
        let zero = h.request("zero_m", json!(-0.00004)).unwrap();
        assert!(zero == 0.0 && zero.is_sign_positive());
        assert_eq!(h.request("zero_m", json!(-100)).unwrap(), -100.0);
        assert_eq!(h.request("zero_m", json!("-0.15")).unwrap(), -0.15);
    }

    #[test]
    fn span_must_stay_above_a_negative_zero() {
        let mut h = Harness::on(json!({}));
        h.request("zero_m", json!(-0.5)).unwrap();
        assert_eq!(h.request("span_m", json!(0.0)).unwrap_err().code, "INVALID");
        assert_eq!(h.request("span_m", json!(0.0001)).unwrap(), 0.0001);
        let err = h.request("zero_m", json!(0.0001)).unwrap_err();
        assert!(err.message.contains("below the span"), "{}", err.message);
    }

    #[test]
    fn zero_range_description() {
        assert_eq!(ZERO.describe_range(), "-100 to below 100 m");
        assert_eq!(SPAN.describe_range(), "above 0 to 100 m");
    }

    #[test]
    fn negative_config_zero_is_the_default_and_restored() {
        let mut h = Harness::on(json!({"sensor_minimum_metres": -0.15}));
        assert_eq!(h.eff().0, s(-0.15));
        assert_eq!(h.tags.value("zero_m"), json!(-0.15));

        let persisted = json!({"zero_m": -0.3, "span_m": 4.5, "fluid_density": 1000.0});
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({}),
            false,
            persisted,
        );
        assert_eq!(h.eff(), (s(-0.3), s(4.5), s(1000.0)));
    }

    #[test]
    fn a_failed_write_changes_nothing_and_reports_it() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"span_m": 6.0}),
            true,
            json!({}),
        );
        h.writer.online = false;
        let before = h.eff();
        let tags = h.tags.store.clone();
        for call in 0..3 {
            let err = match call {
                0 => h.request("span_m", json!(8.0)).unwrap_err(),
                1 => h.request("zero_m", json!(1.0)).unwrap_err(),
                _ => h.reset().unwrap_err(),
            };
            assert_eq!(err.code, "UNAVAILABLE");
            assert_eq!(h.eff(), before);
            h.maintain();
            assert_eq!(h.tags.store, tags);
        }
        h.writer.online = true;
        h.request("span_m", json!(7.0)).unwrap();
        h.echo(None);
        assert_eq!(h.eff().1, s(7.0));
        h.maintain();
        let expected: Map<String, Value> =
            json!({"span_m": 7.0, "span_m_default": null}).as_object().cloned().unwrap();
        assert_eq!(h.writer.aggregate_writes, vec![expected]);
    }

    #[test]
    fn write_goes_to_ui_cmds_and_the_echo_keeps_it() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"alarm_point": 3}),
            true,
            json!({}),
        );
        h.request("span_m", json!(6.0)).unwrap();
        assert_eq!(
            Value::from(h.writer.aggregate_writes.clone()),
            json!([{"span_m": 6.0, "span_m_default": null}])
        );
        h.echo(None);
        assert_eq!(h.eff().1, s(6.0));
    }

    #[test]
    fn two_quick_writes_the_first_echo_does_not_revert() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"alarm_point": 3}),
            true,
            json!({}),
        );
        h.request("span_m", json!(6.0)).unwrap();
        h.request("span_m", json!(7.0)).unwrap();
        h.echo(Some(1));
        assert_eq!(h.eff().1, s(7.0));
        h.echo(None);
        assert_eq!(h.eff().1, s(7.0));
    }

    #[test]
    fn an_unechoed_write_is_forgotten_after_the_timeout() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"span_m": 6.0}),
            true,
            json!({}),
        );
        h.request("span_m", json!(7.0)).unwrap();
        h.writer.written.clear();
        *h.clock.lock().unwrap() += ECHO_TIMEOUT_SECS + 1.0;
        // A later echo of that write is now a direct edit, adopted as one.
        h.ui_set("span_m", json!(7.0));
        assert_eq!(h.eff().1, s(7.0));
        h.ui_set("span_m", json!(5.0));
        assert_eq!(h.eff().1, s(5.0));
    }

    #[test]
    fn direct_ui_cmds_edit_is_adopted_and_invalid_one_ignored() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"span_m": 6.0}),
            true,
            json!({}),
        );
        assert_eq!(h.eff().1, s(6.0));
        h.ui_set("span_m", json!(7.5));
        assert_eq!(h.eff().1, s(7.5));
        h.ui_set("span_m", json!(250));
        assert_eq!(h.eff().1, s(7.5));
    }

    #[test]
    fn inverted_direct_edit_falls_back_to_the_config_pair() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"zero_m": 6.0, "span_m": 5.0}),
            true,
            json!({}),
        );
        let e = h.eff();
        assert_eq!((e.0, e.1), (s(0.0), s(10.0)));
    }

    #[test]
    fn reset_writes_the_config_defaults_and_clears_the_operator_values() {
        let config = make_config(json!({
            "operator_calibration_enabled": true,
            "sensor_maximum_metres": 8.0,
            "fluid_density": 1100,
        }));
        let mut h = Harness::started(config, json!({"zero_m": 1.0, "span_m": 6.0}), true, json!({}));
        assert_eq!(h.eff().0, s(1.0));
        let result = h.reset().unwrap();
        assert_eq!(result.to_json(), json!({"zero_m": 0.0, "span_m": 8.0, "fluid_density": 1100.0}));
        assert_eq!(
            Value::from(h.writer.aggregate_writes.clone()),
            json!([{
                "zero_m": 0.0, "zero_m_default": 0.0,
                "span_m": 8.0, "span_m_default": 8.0,
                "fluid_density": 1100.0, "fluid_density_default": 1100.0,
            }])
        );
        assert_eq!(h.tags.value("span_m"), json!(8.0));
        h.echo(None);
        assert_eq!(h.eff(), (s(0.0), s(8.0), s(1100.0)));
        for name in ["zero_m", "span_m", "fluid_density"] {
            assert_eq!(h.cal.setting(name), None);
        }
    }

    #[test]
    fn after_reset_a_config_change_takes_effect_and_is_written_back() {
        let values = json!({"span_m": 8.0, "span_m_default": 8.0, "zero_m": 0.0});
        let config = make_config(json!({"operator_calibration_enabled": true, "sensor_maximum_metres": 9.0}));
        let mut h = Harness::started(config, values, true, json!({}));
        assert_eq!(h.eff().1, s(9.0));
        h.maintain();
        assert_eq!(
            Value::from(h.writer.aggregate_writes.clone()),
            json!([{"span_m": 9.0, "span_m_default": 9.0}])
        );
        h.echo(None);
        h.maintain();
        assert_eq!(h.writer.aggregate_writes.len(), 1);
        assert_eq!(h.eff().1, s(9.0));
    }

    #[test]
    fn operator_value_after_a_reset_clears_the_marker() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({"alarm_point": 1}),
            true,
            json!({}),
        );
        h.reset().unwrap();
        h.echo(None);
        h.request("span_m", json!(10.0)).unwrap();
        h.echo(None);
        assert!(!h.cal.env.ui_values.contains_key("span_m_default"));
        assert_eq!(h.cal.setting("span_m"), s(10.0));
    }

    #[test]
    fn restart_adopts_the_ui_cmds_values() {
        let values = json!({"zero_m": 0.5, "span_m": 4.5, "fluid_density": 1200.0});
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            values,
            true,
            json!({}),
        );
        assert_eq!(h.eff(), (s(0.5), s(4.5), s(1200.0)));
        assert_eq!(h.tags.value("zero_m"), json!(0.5));
        assert_eq!(h.tags.value("fluid_density"), json!(1200.0));
        assert!(h.writer.aggregate_writes.is_empty());
    }

    #[test]
    fn offline_boot_restores_from_the_tags_until_ui_cmds_delivers() {
        let persisted = json!({"zero_m": 0.5, "span_m": 4.5, "fluid_density": 1000.0});
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({}),
            false,
            persisted,
        );
        assert_eq!(h.eff(), (s(0.5), s(4.5), s(1000.0)));
        assert_eq!(h.cal.setting("fluid_density"), None);
        assert_eq!(h.tags.value("span_m"), json!(4.5));
        h.ui_set("zero_m", json!(0.5));
        h.ui_set("span_m", json!(5.0));
        assert_eq!(h.eff(), (s(0.5), s(5.0), s(1000.0)));
    }

    #[test]
    fn offline_restore_dropped_when_ui_cmds_has_no_values() {
        let persisted = json!({"zero_m": 0.5, "span_m": 4.5});
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({}),
            false,
            persisted,
        );
        assert_eq!(h.eff().1, s(4.5));
        h.ui_set("alarm_point", json!(2.0));
        assert_eq!(h.eff(), (s(0.0), s(10.0), s(1000.0)));
    }

    #[test]
    fn offline_restore_ignores_invalid_tags() {
        let persisted = json!({"zero_m": -150, "span_m": "x", "fluid_density": 9000});
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({}),
            false,
            persisted,
        );
        assert_eq!(h.eff(), (s(0.0), s(10.0), s(1000.0)));
    }

    #[test]
    fn write_before_a_late_sync_wins_and_is_stored_again() {
        let mut h = Harness::started(
            make_config(json!({"operator_calibration_enabled": true})),
            json!({}),
            false,
            json!({}),
        );
        h.request("span_m", json!(6.0)).unwrap();
        h.writer.written.clear();
        h.ui_set("span_m", json!(4.0));
        assert_eq!(h.eff().1, s(6.0));
        h.maintain();
        assert_eq!(
            Value::from(h.writer.aggregate_writes.last().cloned().unwrap()),
            json!({"span_m": 6.0, "span_m_default": null})
        );
        h.echo(None);
        assert_eq!(h.eff().1, s(6.0));
    }

    // -- the calibration driving the conversion (the device app's view) ------

    use crate::conversion::{handle_update, Conversion, FaultTracker};

    const READINGS: [f64; 6] = [4.0, 6.5, 12.0, 17.3, 20.0, 21.0];
    const TYPES: [&str; 3] = ["Submersible", "Radar", "Radar Inverted"];

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-9_f64.max(1e-9 * b.abs())
    }

    impl Harness {
        /// The params the device app uses: the calibration when on, else the config.
        fn app_params(&mut self) -> Params {
            if self.cal.enabled() {
                self.params()
            } else {
                Params::from_config(&self.config)
            }
        }

        fn level(&mut self, reading: f64) -> f64 {
            let p = self.app_params();
            Conversion::new(&self.config, p).level_reading(reading)
        }

        fn pct(&mut self, reading: f64) -> f64 {
            let p = self.app_params();
            Conversion::new(&self.config, p).filled_percentage(reading).unwrap()
        }

        fn vol(&mut self, reading: f64) -> f64 {
            let p = self.app_params();
            Conversion::new(&self.config, p).volume(reading).unwrap()
        }

        fn update(&mut self, fault: &mut FaultTracker, reading: f64) -> Option<f64> {
            let p = self.app_params();
            handle_update(&self.config, p, fault, Some(reading), &mut self.tags)
        }
    }

    fn plain(config: &DeviceConfig) -> Conversion<'_> {
        Conversion::new(config, Params::from_config(config))
    }

    #[test]
    fn off_readings_identical_to_today() {
        for ty in TYPES {
            for density in [None, Some(1250.0)] {
                let mut o = json!({"sensor_type": ty, "sensor_minimum_metres": 0.2});
                if let Some(d) = density {
                    o["fluid_density"] = json!(d);
                }
                let config = make_config(o);
                // Operator values sitting in ui_cmds (from a time it was on) are ignored.
                let mut h = Harness::started(
                    config.clone(),
                    json!({"zero_m": 1.0, "span_m": 3.0, "fluid_density": 900}),
                    true,
                    json!({}),
                );
                let p = plain(&config);
                for r in READINGS {
                    assert_eq!(h.level(r), p.level_reading(r));
                    assert_eq!(h.pct(r), p.filled_percentage(r).unwrap());
                    assert_eq!(h.vol(r), p.volume(r).unwrap());
                }
            }
        }
    }

    #[test]
    fn on_without_operator_values_matches_the_config() {
        let mut h = Harness::on(json!({"fluid_density": 1250.0}));
        let config = h.config.clone();
        for r in READINGS {
            assert_eq!(h.level(r), plain(&config).level_reading(r));
        }
    }

    #[test]
    fn zero_and_span_change_level_and_percentage() {
        let mut h = Harness::on(json!({}));
        assert_eq!(h.request("zero_m", json!(1.0)).unwrap(), 1.0);
        assert_eq!(h.request("span_m", json!(5.0)).unwrap(), 5.0);
        assert!(approx(h.level(12.0), 3.0));
        assert!(approx(h.pct(12.0), 30.0));
        assert!(approx(h.vol(12.0), 300.0));
        let mut fault = FaultTracker::default();
        h.update(&mut fault, 12.0);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 3.0));
        assert!(approx(h.tags.value("level_filled_percentage").as_f64().unwrap(), 30.0));
        assert_eq!(h.tags.value("zero_m"), json!(1.0));
        assert_eq!(h.tags.value("span_m"), json!(5.0));
    }

    #[test]
    fn density_scales_the_submersible_column_above_the_zero() {
        let mut h = Harness::on(json!({}));
        h.request("zero_m", json!(0.5)).unwrap();
        h.request("span_m", json!(10.5)).unwrap();
        h.request("fluid_density", json!(1250)).unwrap();
        assert!(approx(h.level(12.0), 4.5));
        assert!(approx(h.pct(12.0), 45.0));
        assert_eq!(h.tags.value("fluid_density"), json!(1250.0));
    }

    #[test]
    fn radar_ignores_density_but_follows_zero_and_span() {
        let mut h = Harness::on(json!({"sensor_type": "Radar"}));
        h.request("span_m", json!(4.0)).unwrap();
        h.request("fluid_density", json!(1500)).unwrap();
        let other = make_config(json!({"sensor_type": "Radar", "sensor_maximum_metres": 4.0}));
        for r in READINGS {
            assert!(approx(h.level(r), plain(&other).level_reading(r)));
        }
    }

    #[test]
    fn labels_match_the_input_each_value_is_read_at() {
        for (ty, zero_at, span_at) in [("Submersible", 4.0, 20.0), ("Radar", 20.0, 4.0), ("Radar Inverted", 4.0, 20.0)] {
            let mut h = Harness::on(json!({"sensor_type": ty}));
            h.request("zero_m", json!(1.0)).unwrap();
            h.request("span_m", json!(5.0)).unwrap();
            assert!(approx(h.level(zero_at), 1.0), "{ty}");
            assert!(approx(h.level(span_at), 5.0), "{ty}");
        }
    }

    #[test]
    fn level_alarm_follows_the_calibration() {
        let mut h = Harness::on(json!({"reading_type": "Level Reading"}));
        h.request("span_m", json!(5.0)).unwrap();
        let p = h.app_params();
        let v = crate::device::alarm_value(&Conversion::new(&h.config, p), 12.0).unwrap();
        assert!(approx(v, 2.5));
    }

    #[test]
    fn change_applies_on_the_next_reading_without_restart() {
        let mut h = Harness::on(json!({}));
        let mut fault = FaultTracker::default();
        h.update(&mut fault, 12.0);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 5.0));
        h.request("span_m", json!(8.0)).unwrap();
        h.update(&mut fault, 12.0);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 4.0));
    }

    #[test]
    fn negative_zero_reads_below_the_datum_and_derived_values_follow() {
        let mut h = Harness::on(json!({}));
        assert_eq!(h.request("zero_m", json!(-0.15)).unwrap(), -0.15);
        assert_eq!(h.request("span_m", json!(2.0)).unwrap(), 2.0);
        assert!(approx(h.level(4.0), -0.15));
        assert!(approx(h.pct(4.0), -1.5));
        assert!(approx(h.vol(4.0), -15.0));
        assert!(h.level(4.0 + 16.0 * 0.15 / 2.15).abs() < 1e-12);
        assert!(approx(h.level(12.0), 0.925));
        assert!(approx(h.pct(12.0), 9.25));
        assert!(approx(h.level(20.0), 2.0));
        let mut fault = FaultTracker::default();
        h.update(&mut fault, 4.0);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), -0.15));
        assert!(approx(h.tags.value("level_reading_display").as_f64().unwrap(), -0.15));
        assert!(approx(h.tags.value("level_filled_percentage").as_f64().unwrap(), -1.5));
        assert!(approx(h.tags.value("level_volume").as_f64().unwrap(), -15.0));
        assert_eq!(h.tags.value("zero_m"), json!(-0.15));
    }

    #[test]
    fn negative_zero_with_a_volume_curve_extrapolates_the_first_segment() {
        let curve = json!([{"level": 0.0, "volume": 0.0}, {"level": 2.0, "volume": 400.0}]);
        let mut h = Harness::on(json!({"volume_curve": curve}));
        h.request("zero_m", json!(-0.15)).unwrap();
        h.request("span_m", json!(2.0)).unwrap();
        assert!(approx(h.vol(4.0), -30.0));
        assert!(approx(h.pct(4.0), -7.5));
    }

    #[test]
    fn negative_zero_density_scales_only_the_column_above_it() {
        let mut h = Harness::on(json!({}));
        h.request("zero_m", json!(-0.5)).unwrap();
        h.request("span_m", json!(9.5)).unwrap();
        h.request("fluid_density", json!(1250)).unwrap();
        assert!(approx(h.level(4.0), -0.5));
        assert!(approx(h.level(12.0), 3.5));
    }

    #[test]
    fn negative_zero_on_a_radar_is_the_level_at_20_ma() {
        let mut h = Harness::on(json!({"sensor_type": "Radar"}));
        h.request("zero_m", json!(-0.2)).unwrap();
        h.request("span_m", json!(3.0)).unwrap();
        assert!(approx(h.level(20.0), -0.2));
        assert!(approx(h.level(4.0), 3.0));
        assert_eq!(h.request("span_m", json!(-0.1)).unwrap_err().code, "INVALID");
    }

    #[test]
    fn negative_zero_level_alarm() {
        use crate::alarm::{evaluate, Direction};
        use crate::config::AlarmType;
        let mut h = Harness::on(json!({"reading_type": "Level Reading"}));
        h.request("zero_m", json!(-0.15)).unwrap();
        let p = h.app_params();
        let reading = crate::device::alarm_value(&Conversion::new(&h.config, p), 4.0).unwrap();
        assert!(approx(reading, -0.15));
        let breach = evaluate(Some(reading), AlarmType::LessThan, Some(0.1), None, None).unwrap();
        assert_eq!((breach.direction, breach.bound), (Direction::DroppedBelow, 0.1));
        assert_eq!(crate::device::format_value(reading), "-0.15");
        let mut h = Harness::on(json!({"reading_type": "Filled Percentage"}));
        h.request("zero_m", json!(-0.15)).unwrap();
        h.request("span_m", json!(2.0)).unwrap();
        let p = h.app_params();
        let v = crate::device::alarm_value(&Conversion::new(&h.config, p), 4.0).unwrap();
        assert!(approx(v, -1.5));
    }

    #[test]
    fn under_range_loop_current_is_published() {
        for enabled in [true, false] {
            for reading in [3.9, 0.0] {
                let mut h = Harness::on(json!({"operator_calibration_enabled": enabled}));
                let mut fault = FaultTracker::default();
                h.update(&mut fault, 12.0);
                h.update(&mut fault, reading);
                assert_eq!(h.tags.value("raw_level_reading"), json!(reading));
            }
        }
    }

    #[test]
    fn one_fault_level_sample_changes_nothing_derived() {
        for enabled in [true, false] {
            let mut h = Harness::on(json!({"operator_calibration_enabled": enabled}));
            let mut fault = FaultTracker::default();
            assert_eq!(h.update(&mut fault, 12.0), Some(12.0));
            // None: the alarm is not checked for it.
            assert_eq!(h.update(&mut fault, 0.0), None);
            assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 5.0));
            assert_eq!(h.tags.value("sensor_fault"), Value::Null);
        }
    }

    #[test]
    fn clamp_band_alarms_on_the_clamped_reading() {
        let mut h = Harness::on(json!({"operator_calibration_enabled": false}));
        let mut fault = FaultTracker::default();
        assert_eq!(h.update(&mut fault, 3.9), Some(4.0));
    }

    #[test]
    fn calibration_still_works_through_a_fault() {
        let mut h = Harness::on(json!({}));
        let mut fault = FaultTracker::default();
        h.request("zero_m", json!(1.0)).unwrap();
        h.echo(None);
        h.update(&mut fault, 3.9);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 1.0));
        for _ in 0..3 {
            h.maintain();
            assert_eq!(h.update(&mut fault, 0.5), None);
        }
        assert_eq!(h.tags.value("sensor_fault"), json!("under_range"));
        assert_eq!(h.tags.value("level_reading"), Value::Null);
        assert_eq!(h.tags.value("raw_level_reading"), json!(0.5));
        assert_eq!(h.tags.value("zero_m"), json!(1.0));
        assert_eq!(h.tags.value("operator_calibration"), json!(true));
        h.request("zero_m", json!(2.0)).unwrap();
        h.echo(None);
        for _ in 0..3 {
            h.update(&mut fault, 4.0);
        }
        assert_eq!(h.tags.value("sensor_fault"), Value::Null);
        assert!(approx(h.tags.value("level_reading").as_f64().unwrap(), 2.0));
    }
}

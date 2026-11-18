//! The analog level sensor, on the
//! [`doover`](https://github.com/getdoover/doover-rs) client library (it
//! replaced the Python app, whose schemas it keeps byte for byte): read an
//! analog-input pin from the
//! platform interface, convert it to level / filled percentage / volume,
//! publish them as tags, alarm on the configured reading, and let operators
//! calibrate the zero, span and fluid density from the UI.
//!
//! Module map (the Python app it replaced → Rust):
//!
//! | Python                                   | Rust                    |
//! |------------------------------------------|-------------------------|
//! | `common_config.py`, `app_config.py`      | [`config`]              |
//! | `common_tags.py`, `app_tags.py`          | [`tags`]                |
//! | `common_ui.py`, `app_ui.py`              | [`ui`]                  |
//! | `common_app.py`                          | [`conversion`]          |
//! | `alarm.py`                               | [`alarm`]               |
//! | `application.py` (alarm wiring)          | [`device`]              |
//! | `calibration.py`                         | [`calibration`]         |
//! | `app_notifications.py`                   | [`DeviceNotifications`] |
//!
//! Regenerate the schemas (config, UI, notifications) without connecting to
//! anything:
//!
//!   cargo run -- export doover_config.json --app-name analog_level_sensor
//!
//! Run against a live agent:
//!
//!   DDA_URI=127.0.0.1:50051 PLT_URI=127.0.0.1:50053 APP_KEY=analog_level_sensor_1 \
//!     cargo run --release
//!
//! Set SIMULATE_AI=1 to synthesise a noisy 4–20 mA signal instead of reading
//! the platform interface.

mod alarm;
mod calibration;
mod config;
mod conversion;
mod device;
mod tags;
mod ui;
mod util;

use std::sync::Arc;
use std::time::Duration;

use doover::error::Result;
use doover::notifications::NotificationDecl;
use doover::rpc::RpcError;
use doover::{AppContext, Application, Event, EventSubscription, Notifications, PlatformClient};
use serde_json::{json, Map, Value};
use tokio::sync::Mutex;

use crate::alarm::Alarm;
use crate::calibration::{CalibrationConfig, SensorCalibration, UiCmdsWriter};
use crate::config::DeviceConfig;
use crate::conversion::{Conversion, FaultTracker, Params};
use crate::tags::{DeviceTags, PendingTags};
use crate::ui::DeviceUi;

const UI_CMDS: &str = "ui_cmds";

/// `app_notifications.py`. Declaring it publishes a schema with the app, which
/// lets an operator turn this notification off on its own.
#[derive(Notifications)]
#[allow(dead_code)]
pub struct DeviceNotifications {
    /// Sent when the reading crosses the configured alarm setpoint, and again on each renotify interval while it stays there.
    #[notification(
        message = "The level has crossed its alarm setpoint",
        display_name = "Level alarm",
        severity = "warn"
    )]
    level_alarm: NotificationDecl,
}

/// ui_cmds writes for the calibration: one aggregate merge per payload, so a
/// value and its marker arrive in one echo.
struct CtxWriter<'a>(&'a AppContext);

#[doover::async_trait]
impl UiCmdsWriter for CtxWriter<'_> {
    async fn write(&mut self, payload: Map<String, Value>) -> Result<()> {
        let mut data = Map::new();
        data.insert(self.0.app_key().to_string(), Value::Object(payload));
        self.0.update_channel_aggregate(UI_CMDS, &Value::Object(data)).await
    }
}

struct AnalogLevelSensor {
    config: DeviceConfig,
    tags: DeviceTags,
    ui: DeviceUi,
    plt: Option<PlatformClient>,
    fault: FaultTracker,
    alarm: Alarm,
    /// Shared with the calibration RPC handlers, which run on their own tasks.
    calibration: Arc<Mutex<SensorCalibration>>,
    /// SIMULATE_AI: synthesise a noisy in-range mA value.
    simulate: bool,
    rng: u64,
    t: u64,
}

/// Drive the sensor's power pin, when one is configured. (A free function:
/// the app holds its UI, which is not `Sync`, so `&self` cannot cross an
/// await.)
async fn set_power_pin(plt: Option<&PlatformClient>, pin: Option<i64>, high: bool) -> Result<()> {
    if let (Some(pin), Some(plt)) = (pin, plt) {
        plt.set_do(pin as i32, high).await?;
    }
    Ok(())
}

/// ui_cmds as the calibration sees it (pydoover `ui_manager.values`).
async fn store_ui_values(cal: &Mutex<SensorCalibration>, app_key: &str, aggregate: Option<&Value>) {
    let values = aggregate
        .and_then(|d| d.get(app_key))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    cal.lock().await.env.ui_values = values;
}

impl AnalogLevelSensor {

    /// Cheap xorshift64 in [0,1) — enough entropy for visible sensor noise.
    fn next_rand(&mut self) -> f64 {
        let mut x = self.rng;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.rng = x;
        (x >> 11) as f64 / (1u64 << 53) as f64
    }

    /// A plausible 4–20 mA signal: mid-range base + slow drift + noise.
    fn simulated_ma(&mut self) -> f64 {
        self.t = self.t.wrapping_add(1);
        let drift = 6.0 * (self.t as f64 * 0.01).sin();
        let noise = (self.next_rand() - 0.5) * 1.0;
        (12.0 + drift + noise).clamp(4.0, 20.0)
    }

    /// Register the calibration RPCs on ui_cmds (pydoover
    /// `@ui.handler(RPC_PATTERN / RESET_ELEMENT, auto_update=False)`): the
    /// handler stores the value itself and answers `{method: applied}`, or a
    /// typed `INVALID` / `UNAVAILABLE` error.
    fn register_calibration_rpcs(&self, ctx: &AppContext) {
        for spec in calibration::VALUES {
            let (cal, ctx2) = (self.calibration.clone(), ctx.clone());
            ctx.rpc().register(Some(UI_CMDS), spec.name, move |rpc_ctx, value| {
                let (cal, ctx) = (cal.clone(), ctx2.clone());
                async move {
                    let mut cal = cal.lock().await;
                    let mut tags = PendingTags::new(&ctx);
                    let method = rpc_ctx.method.clone();
                    let applied =
                        cal.request(&method, &value, &mut tags, &mut CtxWriter(&ctx)).await?;
                    flush_rpc_tags(tags).await;
                    let mut response = Map::new();
                    response.insert(method, json!(applied));
                    Ok::<Value, RpcError>(Value::Object(response))
                }
            });
        }
        let (cal, ctx2) = (self.calibration.clone(), ctx.clone());
        ctx.rpc().register(Some(UI_CMDS), calibration::RESET_ELEMENT, move |_rpc_ctx, _value| {
            let (cal, ctx) = (cal.clone(), ctx2.clone());
            async move {
                let mut cal = cal.lock().await;
                let mut tags = PendingTags::new(&ctx);
                let effective = cal.reset(&mut tags, &mut CtxWriter(&ctx)).await?;
                flush_rpc_tags(tags).await;
                Ok::<Value, RpcError>(effective.to_json())
            }
        });
    }
}

/// The value is already stored; a readback that fails to buffer is
/// republished by the next loop anyway.
async fn flush_rpc_tags(tags: PendingTags<'_>) {
    if let Err(e) = tags.flush().await {
        tracing::warn!("could not publish the calibration readbacks: {e}");
    }
}

#[doover::async_trait]
impl Application for AnalogLevelSensor {
    type Config = DeviceConfig;
    type Tags = DeviceTags;
    type Ui = DeviceUi;
    type Notifications = DeviceNotifications;

    fn create(config: DeviceConfig, tags: DeviceTags, ui: DeviceUi) -> Self {
        let simulate =
            std::env::var("SIMULATE_AI").map(|v| v != "0" && !v.is_empty()).unwrap_or(false);
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64 | 1)
            .unwrap_or(0x9E3779B97F4A7C15);
        let calibration = SensorCalibration::new(CalibrationConfig::from_config(&config));
        let alarm = Alarm::new(config.raw.alarm_grace_period_s, config.raw.alarm_renotify_interval_s);
        Self {
            config,
            tags,
            ui,
            plt: None,
            fault: FaultTracker::default(),
            alarm,
            calibration: Arc::new(Mutex::new(calibration)),
            simulate,
            rng: seed,
            t: 0,
        }
    }

    fn ui(&self) -> Option<&DeviceUi> {
        Some(&self.ui)
    }

    fn ui_mut(&mut self) -> Option<&mut DeviceUi> {
        Some(&mut self.ui)
    }

    fn loop_target_period(&self) -> Duration {
        // Python setup: `self.loop_target_period = 1 / freq` when freq > 0.
        let freq = self.config.raw.polling_frequency;
        if freq > 0.0 {
            Duration::from_secs_f64(1.0 / freq)
        } else {
            Duration::from_secs(1)
        }
    }

    async fn setup(&mut self, ctx: &AppContext) -> Result<()> {
        if !self.simulate {
            let plt_uri =
                std::env::var("PLT_URI").unwrap_or_else(|_| "127.0.0.1:50053".to_string());
            let plt_uri =
                if plt_uri.contains("://") { plt_uri } else { format!("http://{plt_uri}") };
            tracing::info!("connecting to platform interface at {plt_uri}");
            self.plt = Some(PlatformClient::connect(plt_uri).await?);
        } else {
            tracing::warn!("SIMULATE_AI set: synthesising AI values (platform interface NOT read)");
        }

        set_power_pin(self.plt.as_ref(), self.config.raw.power_pin, true).await?;

        // Operator Sensor Calibration, before the first reading: an offline
        // reboot restores it from the readback tags, which a publish would
        // overwrite.
        ctx.subscribe_filtered(
            UI_CMDS,
            EventSubscription::AGGREGATE_UPDATE | EventSubscription::CHANNEL_SYNC,
        );
        let synced = if self.config.raw.operator_calibration_enabled {
            let timeout = Duration::from_secs(calibration::UI_CMDS_SYNC_TIMEOUT_SECS);
            let synced = ctx.wait_for_channels_sync(&[UI_CMDS], timeout).await;
            if synced {
                match ctx.fetch_channel_data(UI_CMDS).await {
                    Ok(agg) => store_ui_values(&self.calibration, ctx.app_key(), agg.as_ref()).await,
                    Err(e) => tracing::warn!("could not read ui_cmds: {e}"),
                }
            }
            synced
        } else {
            false
        };
        {
            let mut tags = PendingTags::new(ctx);
            self.calibration.lock().await.startup(synced, &mut tags);
            tags.flush().await?;
        }
        self.register_calibration_rpcs(ctx);

        // UI.setup(): the runtime mutations from the config.
        ui::setup(&mut self.ui, &self.tags, &self.config);
        Ok(())
    }

    async fn main_loop(&mut self, ctx: &AppContext) -> Result<()> {
        let mut tags = PendingTags::new(ctx);
        let params = {
            let mut cal = self.calibration.lock().await;
            cal.maintain(&mut tags, &mut CtxWriter(ctx)).await;
            let config_params = Params::from_config(&self.config);
            if cal.enabled() {
                cal.effective().params(config_params)
            } else {
                config_params
            }
        };

        let result = if self.simulate {
            self.simulated_ma()
        } else {
            let pin = self.config.raw.ai_pin as i32;
            self.plt.as_ref().expect("platform connected in setup").fetch_ai(pin).await? as f64
        };

        set_power_pin(self.plt.as_ref(), self.config.raw.power_pin, true).await?;

        let reading =
            conversion::handle_update(&self.config, params, &mut self.fault, Some(result), &mut tags);

        // No level from this sample (a fault, or an under-range sample not
        // yet one): the alarm stays blind to it too.
        if let Some(reading) = reading {
            let conv = Conversion::new(&self.config, params);
            let point = ctx.ui().get_value(&self.ui.alarm_point.interaction.element.name);
            let range = ctx.ui().get_value(&self.ui.alarm_range.interaction.element.name);
            let display_name = ctx.app_display_name();
            if let Some(message) = device::check_alarm(
                &mut self.alarm,
                &conv,
                reading,
                point.as_ref(),
                range.as_ref(),
                &display_name,
            ) {
                // No title: the server substitutes the agent's display name.
                ctx.notify(DeviceNotifications::LEVEL_ALARM.message(message)).await?;
            }
        }

        tags.flush().await
    }

    async fn on_aggregate_update(&mut self, ctx: &AppContext, event: &Event) -> Result<()> {
        if event.channel == UI_CMDS {
            store_ui_values(&self.calibration, ctx.app_key(), event.aggregate_data()).await;
        }
        Ok(())
    }

    async fn on_channel_sync(&mut self, ctx: &AppContext, event: &Event) -> Result<()> {
        if event.channel == UI_CMDS {
            store_ui_values(&self.calibration, ctx.app_key(), event.aggregate_data()).await;
        }
        Ok(())
    }

    async fn on_config_update(&mut self, _ctx: &AppContext, config: DeviceConfig) -> Result<()> {
        // pydoover re-injects the config and the app reads it live; the UI
        // and the alarm's timings stay as set up until the next restart.
        self.calibration.lock().await.env.config = CalibrationConfig::from_config(&config);
        self.config = config;
        Ok(())
    }

    async fn on_shutdown(&mut self, _ctx: &AppContext) -> Result<()> {
        // Python `on_shutdown_at`: de-power the sensor.
        set_power_pin(self.plt.as_ref(), self.config.raw.power_pin, false).await
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    tracing_subscriber::fmt().with_env_filter(filter).init();
    doover::run::<AnalogLevelSensor>().await
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The schemas the Python app published (`export-config`, `export-ui`,
    /// `export-notifications`), frozen when this app replaced it: existing
    /// installs keep exactly the config, UI and notifications they had. A
    /// deliberate schema change updates this file with it.
    const PYTHON_EXPORT: &str = include_str!("../tests/fixtures/python_export.json");

    #[test]
    fn export_is_identical_to_the_python_apps() {
        let path = std::env::temp_dir().join(format!("als-export-{}.json", std::process::id()));
        std::fs::write(&path, "{}").unwrap();
        doover::docker::application::write_export::<AnalogLevelSensor>(&path, "analog_level_sensor")
            .unwrap();
        let ours: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let _ = std::fs::remove_file(&path);
        let python: Value = serde_json::from_str(PYTHON_EXPORT).unwrap();
        for key in ["config_schema", "ui_schema", "notification_schema"] {
            // Compared as text, so key order counts too.
            assert_eq!(
                serde_json::to_string_pretty(&ours["analog_level_sensor"][key]).unwrap(),
                serde_json::to_string_pretty(&python[key]).unwrap(),
                "{key} differs from the Python export"
            );
        }
    }
}

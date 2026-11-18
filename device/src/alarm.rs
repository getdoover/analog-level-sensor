//! The pure alarm logic — `analog_level_sensor/alarm.py`: [`evaluate`] and
//! the debouncing [`Alarm`].

use std::time::Instant;

use crate::config::AlarmType;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Exceeded,
    DroppedBelow,
}

impl Direction {
    pub fn as_str(self) -> &'static str {
        match self {
            Direction::Exceeded => "exceeded",
            Direction::DroppedBelow => "dropped below",
        }
    }
}

/// A reading that has crossed one of its configured bounds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Breach {
    pub direction: Direction,
    pub bound: f64,
}

impl Breach {
    fn new(direction: Direction, bound: f64) -> Option<Self> {
        Some(Self { direction, bound })
    }
}

/// Check a reading against its bounds, without regard for timing. `point` is
/// used by the single-sided modes, `low` / `high` by Allowed Range. `None`
/// when the reading is within bounds or the bound it needs is unset. An
/// Allowed Range breach reports the bound actually crossed.
pub fn evaluate(
    value: Option<f64>,
    alarm_type: AlarmType,
    point: Option<f64>,
    low: Option<f64>,
    high: Option<f64>,
) -> Option<Breach> {
    let value = value?;
    match alarm_type {
        AlarmType::GreaterThan => match point {
            Some(p) if value > p => Breach::new(Direction::Exceeded, p),
            _ => None,
        },
        AlarmType::LessThan => match point {
            Some(p) if value < p => Breach::new(Direction::DroppedBelow, p),
            _ => None,
        },
        AlarmType::AllowedRange => {
            if let Some(h) = high.filter(|h| value > *h) {
                return Breach::new(Direction::Exceeded, h);
            }
            if let Some(l) = low.filter(|l| value < *l) {
                return Breach::new(Direction::DroppedBelow, l);
            }
            None
        }
    }
}

/// Debounces breaches into notifications. A breach must persist for
/// `grace_period` seconds before it notifies; while it continues it
/// re-notifies every `renotify_interval` seconds. Returning within bounds
/// clears it, so the next breach serves its own grace period.
#[derive(Debug, Clone)]
pub struct Alarm {
    pub grace_period: f64,
    pub renotify_interval: f64,
    breach_since: Option<f64>,
    last_notified: Option<f64>,
    pub active: bool,
    epoch: Instant,
}

impl Default for Alarm {
    fn default() -> Self {
        Self::new(30.0, 900.0)
    }
}

impl Alarm {
    pub fn new(grace_period: f64, renotify_interval: f64) -> Self {
        Self {
            grace_period,
            renotify_interval,
            breach_since: None,
            last_notified: None,
            active: false,
            epoch: Instant::now(),
        }
    }

    /// Monotonic seconds, so a clock adjustment can't strand an alarm in its
    /// grace period.
    fn now(&self) -> f64 {
        self.epoch.elapsed().as_secs_f64()
    }

    pub fn clear(&mut self) {
        self.breach_since = None;
        self.last_notified = None;
        self.active = false;
    }

    /// Feed a breach (or `None`). True when the caller should notify.
    pub fn update(&mut self, breach: Option<Breach>) -> bool {
        let now = self.now();
        self.update_at(breach, now)
    }

    /// [`update`](Self::update) at an explicit monotonic time (seconds).
    pub fn update_at(&mut self, breach: Option<Breach>, now: f64) -> bool {
        let Some(breach) = breach else {
            if self.active {
                tracing::info!("Alarm cleared");
            }
            self.clear();
            return false;
        };

        let since = *self.breach_since.get_or_insert_with(|| {
            tracing::debug!(
                "Breach started: {} {}",
                breach.direction.as_str(),
                breach.bound
            );
            now
        });
        if now - since < self.grace_period {
            return false;
        }
        if let Some(last) = self.last_notified {
            if now - last < self.renotify_interval {
                return false;
            }
        }
        self.active = true;
        self.last_notified = Some(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GT: AlarmType = AlarmType::GreaterThan;
    const LT: AlarmType = AlarmType::LessThan;
    const RANGE: AlarmType = AlarmType::AllowedRange;

    fn breach(direction: Direction, bound: f64) -> Option<Breach> {
        Some(Breach { direction, bound })
    }

    /// An alarm driven by a hand-advanced clock (`FakeClock` in the Python tests).
    struct Clocked {
        alarm: Alarm,
        now: f64,
    }

    impl Clocked {
        fn new(grace: f64, renotify: f64) -> Self {
            Self {
                alarm: Alarm::new(grace, renotify),
                now: 1000.0,
            }
        }
        fn update(&mut self, b: Option<Breach>) -> bool {
            self.alarm.update_at(b, self.now)
        }
        fn advance(&mut self, s: f64) {
            self.now += s;
        }
    }

    #[test]
    fn evaluate_none_reading_is_silent() {
        assert_eq!(evaluate(None, GT, Some(10.0), None, None), None);
    }

    #[test]
    fn evaluate_unset_bound_is_silent() {
        assert_eq!(evaluate(Some(50.0), GT, None, None, None), None);
        assert_eq!(evaluate(Some(50.0), LT, None, None, None), None);
        assert_eq!(evaluate(Some(50.0), RANGE, None, None, None), None);
    }

    #[test]
    fn evaluate_greater_than() {
        assert_eq!(
            evaluate(Some(11.0), GT, Some(10.0), None, None),
            breach(Direction::Exceeded, 10.0)
        );
        assert_eq!(evaluate(Some(10.0), GT, Some(10.0), None, None), None);
        assert_eq!(evaluate(Some(9.0), GT, Some(10.0), None, None), None);
    }

    #[test]
    fn evaluate_less_than() {
        assert_eq!(
            evaluate(Some(9.0), LT, Some(10.0), None, None),
            breach(Direction::DroppedBelow, 10.0)
        );
        assert_eq!(evaluate(Some(10.0), LT, Some(10.0), None, None), None);
        assert_eq!(evaluate(Some(11.0), LT, Some(10.0), None, None), None);
    }

    #[test]
    fn evaluate_allowed_range_reports_the_crossed_bound() {
        let (low, high) = (Some(1000.0), Some(4000.0));
        assert_eq!(
            evaluate(Some(4500.0), RANGE, None, low, high),
            breach(Direction::Exceeded, 4000.0)
        );
        assert_eq!(
            evaluate(Some(300.0), RANGE, None, low, high),
            breach(Direction::DroppedBelow, 1000.0)
        );
        assert_eq!(evaluate(Some(2000.0), RANGE, None, low, high), None);
    }

    #[test]
    fn alarm_waits_out_the_grace_period() {
        let mut a = Clocked::new(30.0, 900.0);
        let b = breach(Direction::Exceeded, 10.0);
        assert!(!a.update(b));
        a.advance(29.0);
        assert!(!a.update(b));
        a.advance(1.0);
        assert!(a.update(b));
        assert!(a.alarm.active);
    }

    #[test]
    fn alarm_renotifies_on_interval() {
        let mut a = Clocked::new(0.0, 900.0);
        let b = breach(Direction::Exceeded, 10.0);
        assert!(a.update(b));
        a.advance(899.0);
        assert!(!a.update(b));
        a.advance(1.0);
        assert!(a.update(b));
    }

    #[test]
    fn clearing_rearms_a_full_grace_period() {
        let mut a = Clocked::new(30.0, 900.0);
        let b = breach(Direction::Exceeded, 10.0);
        a.advance(30.0);
        assert!(!a.update(b));
        a.advance(30.0);
        assert!(a.update(b));
        assert!(!a.update(None));
        assert!(!a.alarm.active);
        assert!(!a.update(b));
        a.advance(30.0);
        assert!(a.update(b));
    }

    #[test]
    fn alarm_within_bounds_never_notifies() {
        let mut a = Clocked::new(0.0, 900.0);
        for _ in 0..5 {
            assert!(!a.update(None));
            a.advance(60.0);
        }
        assert!(!a.alarm.active);
    }

    #[test]
    fn zero_and_nonzero_grace_both_debounce_a_flapping_reading() {
        for grace in [0.0, 30.0] {
            let mut a = Clocked::new(grace, 900.0);
            let b = breach(Direction::Exceeded, 10.0);
            let mut notifications = 0;
            for _ in 0..10 {
                a.update(b);
                if a.update(None) {
                    notifications += 1;
                }
                a.advance(1.0);
            }
            assert_eq!(notifications, 0);
        }
    }
}

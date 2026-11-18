//! How the app reads its input, and how well its loop keeps up. A test knob,
//! not part of the app's config (its schema stays the Python app's):
//!
//! - `ALS_READ_MODE=image` (default): follow the platform interface's
//!   process image (`watch_image`) and scan once per new image, at most
//!   `polling_frequency` times a second. On a Doovit 2 at 50 Hz that is every
//!   board frame, about 1-2 ms after it arrives.
//! - `ALS_READ_MODE=poll`: the Python app's way, one `fetch_ai` per loop on
//!   the app's own timer.
//!
//! Every 10 s the loop logs one stats line: loops per second, the loop's own
//! work time, and (image mode) how old the image was at each scan and how
//! many images it skipped.

use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReadMode {
    Image,
    Poll,
}

impl ReadMode {
    pub fn from_env() -> Self {
        match std::env::var("ALS_READ_MODE").as_deref() {
            Ok("poll") => ReadMode::Poll,
            _ => ReadMode::Image,
        }
    }
}

/// The fastest the platform interface delivers images (a Doovit 2's frame).
pub const FRAME: Duration = Duration::from_millis(20);

/// `max_rate_hz` for `watch_image`: none at or above the frame rate (every
/// image; a cap there would only add a sleep that pushes the scan off the
/// frames), else `polling_frequency`.
pub fn max_rate_hz(polling_frequency: f64) -> Option<f32> {
    let frame_hz = 1.0 / FRAME.as_secs_f64();
    (polling_frequency > 0.0 && polling_frequency < frame_hz).then_some(polling_frequency as f32)
}

const WINDOW: Duration = Duration::from_secs(10);

pub struct ScanStats {
    mode: ReadMode,
    since: Instant,
    loops: u64,
    work_ms: Vec<f64>,
    age_ms: Vec<f64>,
    skipped: u64,
    last_sequence: Option<u64>,
}

impl ScanStats {
    pub fn new(mode: ReadMode) -> Self {
        Self {
            mode,
            since: Instant::now(),
            loops: 0,
            work_ms: Vec::new(),
            age_ms: Vec::new(),
            skipped: 0,
            last_sequence: None,
        }
    }

    /// One image-mode scan: the image's sequence and its age when the scan began.
    pub fn image(&mut self, sequence: u64, age: Duration) {
        if let Some(last) = self.last_sequence {
            self.skipped += sequence.saturating_sub(last + 1);
        }
        self.last_sequence = Some(sequence);
        self.age_ms.push(age.as_secs_f64() * 1000.0);
    }

    /// One finished loop, `work` being the time it took once it had its input.
    pub fn loop_done(&mut self, work: Duration) {
        self.loops += 1;
        self.work_ms.push(work.as_secs_f64() * 1000.0);
        let elapsed = self.since.elapsed();
        if elapsed < WINDOW {
            return;
        }
        let rate = self.loops as f64 / elapsed.as_secs_f64();
        let work = summary(&mut self.work_ms);
        match self.mode {
            ReadMode::Image => tracing::info!(
                "scan stats (image): {rate:.1} loops/s; work ms {work}; image age at scan ms {}; images skipped {}",
                summary(&mut self.age_ms),
                self.skipped
            ),
            ReadMode::Poll => {
                tracing::info!("scan stats (poll): {rate:.1} loops/s; work ms {work} (fetch_ai included)")
            }
        }
        let last_sequence = self.last_sequence;
        *self = Self::new(self.mode);
        self.last_sequence = last_sequence;
    }
}

fn summary(v: &mut [f64]) -> String {
    if v.is_empty() {
        return "-".into();
    }
    v.sort_by(|a, b| a.total_cmp(b));
    let at = |q: f64| v[((v.len() as f64 * q) as usize).min(v.len() - 1)];
    format!("p50 {:.2} p99 {:.2} max {:.2}", at(0.5), at(0.99), at(1.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_image_at_or_above_the_frame_rate() {
        assert_eq!(max_rate_hz(50.0), None);
        assert_eq!(max_rate_hz(200.0), None);
        assert_eq!(max_rate_hz(10.0), Some(10.0));
        assert_eq!(max_rate_hz(0.0), None);
    }

    #[test]
    fn counts_skipped_images() {
        let mut s = ScanStats::new(ReadMode::Image);
        for seq in [10, 11, 13, 14, 18] {
            s.image(seq, Duration::from_millis(1));
        }
        assert_eq!(s.skipped, 1 + 3);
    }
}

//! Time-based idle gestures, independent of rendering and reproducibly seedable.
pub(super) struct IdleGestures {
    rng: u64,
    clock: f32,
    next_look: f32,
    look_start: Option<f32>,
    turn_in: f32,
    hold: f32,
    turn_out: f32,
    next_blink: f32,
    blink_start: Option<f32>,
    blink_duration: f32,
    enabled: bool,
    pub yaw: f32,
    pub pitch: f32,
    pub closure: f32,
    phase: f32,
    next_kick: f32,
    kick_start: Option<f32>,
    pub feet: [f32; 2],
    next_stretch: f32,
    stretch_start: Option<f32>,
    pub stretch: f32,
    next_extra: f32,
    extra_start: Option<f32>,
    pub extra_kind: u8, // 1: wave, 2: check wrist display
    pub extra: f32,
    pub extra_age: f32,
}

impl IdleGestures {
    pub fn new(seed: u64) -> Self {
        let mut s = Self {
            rng: seed.max(1),
            clock: 0.0,
            next_look: 0.0,
            look_start: None,
            turn_in: 0.0,
            hold: 0.0,
            turn_out: 0.0,
            next_blink: 0.0,
            blink_start: None,
            blink_duration: 0.2,
            enabled: true,
            yaw: 0.0,
            pitch: 0.0,
            closure: 0.0,
            phase: 0.0,
            next_kick: 0.0,
            kick_start: None,
            feet: [0.0; 2],
            next_stretch: 0.0,
            stretch_start: None,
            stretch: 0.0,
            next_extra: 0.0,
            extra_start: None,
            extra_kind: 0,
            extra: 0.0,
            extra_age: 0.0,
        };
        s.next_look = s.random(4.0, 8.0);
        s.next_blink = s.random(2.0, 5.0);
        s.phase = s.random(0.0, std::f32::consts::TAU);
        s.next_kick = s.random(3.0, 7.0);
        s.next_stretch = s.random(11.0, 17.0);
        s.next_extra = s.random(20.0, 25.0);
        s
    }

    fn random(&mut self, min: f32, max: f32) -> f32 {
        self.rng ^= self.rng >> 12;
        self.rng ^= self.rng << 25;
        self.rng ^= self.rng >> 27;
        let bits = self.rng.wrapping_mul(0x2545F4914F6CDD1D);
        min + (max - min) * ((bits >> 40) as f32 / 16_777_216.0)
    }

    pub fn update(&mut self, dt: f32, enabled: bool) {
        self.clock += dt;
        if !enabled {
            self.stretch_start = None;
            self.stretch *= (-dt * 8.0).exp();
        } else {
            if !self.enabled {
                self.next_stretch = self.clock + self.random(11.0, 17.0);
            }
            if self.stretch_start.is_none()
                && self.clock >= self.next_stretch
                && self.look_start.is_none()
                && self.kick_start.is_none()
                && self.extra_start.is_none()
                && self.extra < 0.01
                && self.yaw.abs() < 0.08
            {
                self.stretch_start = Some(self.clock);
            }
            if let Some(start) = self.stretch_start {
                let age = self.clock - start;
                let u = (age / 1.15).min((4.3 - age) / 1.35).clamp(0.0, 1.0);
                self.stretch = u * u * (3.0 - 2.0 * u);
                if age >= 4.3 {
                    self.stretch_start = None;
                    self.stretch = 0.0;
                    self.next_stretch = self.clock + self.random(18.0, 32.0);
                    self.next_kick = self.clock + self.random(3.0, 7.0);
                    self.next_look = self.clock + self.random(3.0, 7.0);
                }
            }
        }
        if !enabled {
            self.extra_start = None;
            self.extra *= (-dt * 9.0).exp();
        } else {
            if !self.enabled {
                self.next_extra = self.clock + self.random(12.0, 20.0);
            }
            if self.extra_start.is_none()
                && self.clock >= self.next_extra
                && self.stretch_start.is_none()
                && self.stretch < 0.01
                && self.look_start.is_none()
                && self.kick_start.is_none()
                && self.yaw.abs() < 0.08
            {
                self.extra_start = Some(self.clock);
                self.extra_kind = if self.random(0.0, 1.0) < 0.5 { 1 } else { 2 };
            }
            if let Some(start) = self.extra_start {
                self.extra_age = self.clock - start;
                let u = (self.extra_age / 0.75)
                    .min((3.8 - self.extra_age) / 0.85)
                    .clamp(0.0, 1.0);
                self.extra = u * u * (3.0 - 2.0 * u);
                if self.extra_age >= 3.8 {
                    self.extra_start = None;
                    self.extra = 0.0;
                    self.next_extra = self.clock + self.random(9.0, 17.0);
                    self.next_look = self.clock + self.random(3.0, 6.0);
                    self.next_kick = self.clock + self.random(3.0, 6.0);
                }
            }
        }
        if !enabled {
            self.kick_start = None;
            for foot in &mut self.feet {
                *foot *= (-dt * 10.0).exp();
            }
        } else {
            if !self.enabled {
                self.next_kick = self.clock + self.random(3.0, 7.0);
            }
            if self.kick_start.is_none()
                && self.clock >= self.next_kick
                && self.stretch_start.is_none()
                && self.clock < self.next_stretch
                && self.extra_start.is_none()
                && self.clock < self.next_extra
            {
                self.kick_start = Some(self.clock);
            }
            if let Some(start) = self.kick_start {
                let age = self.clock - start;
                let envelope = (age / 0.5).min((3.6 - age) / 0.6).clamp(0.0, 1.0);
                for (i, foot) in self.feet.iter_mut().enumerate() {
                    *foot = (age * 5.0 + i as f32 * std::f32::consts::PI).sin() * 0.45 * envelope;
                }
                if age >= 3.6 {
                    self.kick_start = None;
                    self.feet = [0.0; 2];
                    self.next_kick = self.clock + self.random(6.0, 14.0);
                }
            }
        }
        if enabled != self.enabled {
            self.look_start = None;
            self.next_look = self.clock + self.random(4.0, 8.0);
            self.enabled = enabled;
        }
        if enabled {
            if self.look_start.is_none()
                && self.clock >= self.next_look
                && self.stretch_start.is_none()
                && self.clock < self.next_stretch
                && self.extra_start.is_none()
                && self.clock < self.next_extra
            {
                self.look_start = Some(self.clock);
                self.turn_in = self.random(0.65, 1.0);
                self.hold = self.random(2.5, 4.5);
                self.turn_out = self.random(0.75, 1.2);
                // Blink after making eye contact, rather than mid-turn.
                self.next_blink = self.clock + self.turn_in + self.random(0.55, 1.1);
            }
            let mut amount = 0.0;
            if let Some(start) = self.look_start {
                let age = self.clock - start;
                let smooth = |x: f32| {
                    let x = x.clamp(0.0, 1.0);
                    x * x * (3.0 - 2.0 * x)
                };
                amount = if age < self.turn_in {
                    smooth(age / self.turn_in)
                } else if age < self.turn_in + self.hold {
                    1.0
                } else {
                    1.0 - smooth((age - self.turn_in - self.hold) / self.turn_out)
                };
                if age >= self.turn_in + self.hold + self.turn_out {
                    self.look_start = None;
                    self.next_look = self.clock + self.random(7.0, 16.0);
                }
            }
            // Face the +X/-Z camera, with a slight upward gaze and tiny drift.
            let drift = (self.clock * 1.1 + self.phase).sin() * 0.018;
            if amount > 0.0 {
                self.yaw = amount * (std::f32::consts::PI * 0.75 + drift);
                self.pitch = amount * (-0.40 + (self.clock * 1.7 + self.phase).sin() * 0.018);
            } else {
                self.yaw *= (-dt * 6.0).exp();
                self.pitch *= (-dt * 6.0).exp();
            }
        } else {
            let decay = (-dt * 6.0).exp();
            self.yaw *= decay;
            self.pitch *= decay;
        }
        // Normal blinks have irregular gaps and a smooth close/open arc.
        if self.blink_start.is_none() && self.clock >= self.next_blink {
            self.blink_start = Some(self.clock);
            self.blink_duration = self.random(0.18, 0.26);
            self.next_blink = self.clock + self.random(2.6, 6.2);
        }
        self.closure = 0.0;
        if let Some(start) = self.blink_start {
            let progress = (self.clock - start) / self.blink_duration;
            if progress >= 1.0 {
                self.blink_start = None;
            } else {
                self.closure = (std::f32::consts::PI * progress).sin().powi(2);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wave_and_wrist_check_are_random_exclusive_and_interruptible() {
        let mut a = IdleGestures::new(7);
        let mut b = IdleGestures::new(7);
        let mut seen = [false; 3];
        for _ in 0..18000 {
            a.update(1.0 / 30.0, true);
            b.update(1.0 / 30.0, true);
            assert_eq!(a.extra_kind, b.extra_kind);
            assert_eq!(a.extra, b.extra);
            if a.extra > 0.1 {
                seen[a.extra_kind as usize] = true;
                assert!(
                    a.stretch_start.is_none() && a.look_start.is_none() && a.kick_start.is_none()
                );
            }
        }
        assert!(seen[1] && seen[2]);
        a.extra = 1.0;
        a.update(1.0 / 30.0, false);
        assert!(a.extra > 0.0 && a.extra < 1.0);
        for _ in 0..30 {
            a.update(1.0 / 30.0, false);
        }
        assert!(a.extra < 0.001);
    }

    #[test]
    fn stretches_before_sleep_without_other_idle_actions_and_yields() {
        for seed in [1, 7, 42, 98765] {
            let mut s = IdleGestures::new(seed);
            let mut seen = false;
            let mut previous = 0.0;
            for _ in 0..40 * 30 {
                s.update(1.0 / 30.0, true);
                assert!((s.stretch - previous).abs() < 0.05);
                previous = s.stretch;
                if s.stretch > 0.9 {
                    seen = true;
                    assert!(s.look_start.is_none() && s.kick_start.is_none());
                    assert!(s.feet.iter().all(|v| v.abs() < 0.001));
                }
            }
            assert!(seen, "seed {seed} never stretched before sleep");
            s.stretch = 1.0;
            s.update(1.0 / 30.0, false);
            assert!(s.stretch > 0.0 && s.stretch < 1.0);
            for _ in 0..30 {
                s.update(1.0 / 30.0, false);
            }
            assert!(s.stretch < 0.001);
            s.update(1.0 / 30.0, true);
            assert!(s.stretch_start.is_none());
        }
    }

    #[test]
    fn idle_feet_alternate_and_settle_on_interruption() {
        let mut s = IdleGestures::new(7);
        let mut seen = false;
        for _ in 0..450 {
            s.update(1.0 / 30.0, true);
            if s.feet[0].abs() > 0.1 {
                seen = true;
                assert!((s.feet[0] + s.feet[1]).abs() < 0.0001);
            }
        }
        assert!(seen);
        for _ in 0..30 {
            s.update(1.0 / 30.0, false);
        }
        assert!(s.feet.iter().all(|v| v.abs() < 0.001));
    }

    #[test]
    fn holds_eye_contact_and_blinks_for_multiple_seeds_and_frame_rates() {
        for seed in [1, 7, 42, 98765] {
            for fps in [20, 30, 60] {
                let mut s = IdleGestures::new(seed);
                let mut held = 0.0;
                let mut blinked = false;
                for _ in 0..(15 * fps) {
                    s.update(1.0 / fps as f32, true);
                    if let Some(start) = s.look_start {
                        let age = s.clock - start;
                        if age >= s.turn_in && age < s.turn_in + s.hold {
                            held += 1.0 / fps as f32;
                            assert!((s.yaw - std::f32::consts::PI * 0.75).abs() < 0.025);
                            assert!(s.pitch < -0.35);
                            blinked |= s.closure > 0.8;
                        }
                    }
                }
                assert!(
                    held >= 2.4 && held <= 4.6,
                    "seed={seed} fps={fps} held={held}"
                );
                assert!(blinked, "must blink during eye contact");
                assert!(s.yaw.abs() < 0.02);
            }
        }
    }

    #[test]
    fn interruptions_return_smoothly_and_resume_after_a_fresh_delay() {
        let mut s = IdleGestures::new(7);
        while s.yaw < 2.3 {
            s.update(1.0 / 30.0, true);
        }
        let yaw = s.yaw;
        s.update(1.0 / 30.0, false);
        assert!(s.yaw > 0.7 * yaw && s.yaw < yaw);
        for _ in 0..30 {
            s.update(1.0 / 30.0, false);
        }
        assert!(s.yaw < 0.01);
        for _ in 0..90 {
            s.update(1.0 / 30.0, true);
        }
        assert!(s.look_start.is_none());
    }

    #[test]
    fn random_intervals_vary_but_a_seed_is_reproducible() {
        let mut a = IdleGestures::new(42);
        let mut b = IdleGestures::new(42);
        let mut starts = Vec::new();
        let mut previous = None;
        for _ in 0..3600 {
            a.update(1.0 / 30.0, true);
            b.update(1.0 / 30.0, true);
            assert_eq!((a.yaw, a.pitch, a.closure), (b.yaw, b.pitch, b.closure));
            if a.look_start.is_some() && a.look_start != previous {
                starts.push(a.clock);
            }
            previous = a.look_start;
        }
        assert!(starts.len() >= 4);
        let gaps: Vec<f32> = starts.windows(2).map(|v| v[1] - v[0]).collect();
        assert!(gaps.iter().any(|g| (g - gaps[0]).abs() > 0.5));
    }
}
/// Quiet work breaks, independent of the application's actual thinking state.
pub struct WorkGestures {
    rng: u64,
    wait: f32,
    pub age: f32,
    pub kind: u8, // 0: typing, 1: thinking, 2: drinking
    pub weight: f32,
}

impl WorkGestures {
    pub fn new(seed: u64) -> Self {
        let mut value = Self {
            rng: seed.max(1),
            wait: 0.0,
            age: 0.0,
            kind: 0,
            weight: 0.0,
        };
        value.wait = 7.0 + value.random() * 9.0;
        value
    }

    fn random(&mut self) -> f32 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        (self.rng >> 40) as f32 / 16777216.0
    }

    pub fn update(&mut self, dt: f32, enabled: bool) {
        if !enabled {
            // Keep the current pose while blending out, including the held cup.
            self.weight = (self.weight - dt * 4.0).max(0.0);
            if self.weight == 0.0 && self.kind != 0 {
                self.kind = 0;
                self.wait = 7.0 + self.random() * 9.0;
            }
            return;
        }
        if self.kind == 0 {
            self.wait -= dt;
            if self.wait <= 0.0 {
                self.kind = if self.random() < 0.5 { 1 } else { 2 };
                self.age = 0.0;
            }
        } else {
            self.age += dt;
            let duration = if self.kind == 1 { 3.8 } else { 7.0 };
            self.weight = (self.age / 0.45)
                .min((duration - self.age) / 0.5)
                .clamp(0.0, 1.0);
            if self.age >= duration {
                self.kind = 0;
                self.wait = 8.0 + self.random() * 12.0;
            }
        }
    }
}

#[cfg(test)]
mod work_tests {
    use super::*;
    #[test]
    fn work_breaks_vary_pause_and_yield_to_real_activity() {
        let mut a = WorkGestures::new(712);
        let mut b = WorkGestures::new(712);
        let mut seen = [false; 3];
        for _ in 0..18000 {
            a.update(1.0 / 30.0, true);
            b.update(1.0 / 30.0, true);
            assert_eq!(a.kind, b.kind);
            assert_eq!(a.weight, b.weight);
            seen[a.kind as usize] = true;
        }
        assert!(seen.iter().all(|v| *v));
        a.kind = 2;
        a.age = 3.0;
        a.weight = 1.0;
        a.update(0.1, false);
        assert!(a.weight > 0.0 && a.weight < 1.0);
        for _ in 0..20 {
            a.update(0.1, false);
        }
        assert_eq!(a.kind, 0);
        assert_eq!(a.weight, 0.0);
    }
}
/// Longer idle choices alternate a short arcade session and a proper nap.
pub(super) struct IdlePastime {
    rng: u64,
    remaining: f32,
    pub gaming: bool,
}

impl IdlePastime {
    pub fn new(seed: u64) -> Self {
        Self {
            rng: seed.max(1),
            remaining: 0.0,
            gaming: false,
        }
    }
    fn duration(&mut self) -> f32 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        let random = (self.rng >> 40) as f32 / 16777216.0;
        if self.gaming {
            18.0 + random * 10.0
        } else {
            35.0 + random * 25.0
        }
    }
    pub fn update(&mut self, dt: f32, eligible: bool) {
        if !eligible {
            self.remaining = 0.0;
            self.gaming = false;
            return;
        }
        if self.remaining <= 0.0 {
            self.gaming = self.rng & 1 == 0;
            self.remaining = self.duration();
        }
        self.remaining -= dt;
        if self.remaining <= 0.0 {
            self.gaming = !self.gaming;
            self.remaining = self.duration();
        }
    }
}

#[cfg(test)]
mod pastime_tests {
    use super::*;
    #[test]
    fn random_first_choice_then_alternates_and_interrupts() {
        let mut game = IdlePastime::new(2);
        let mut nap = IdlePastime::new(7);
        game.update(0.0, true);
        nap.update(0.0, true);
        assert!(game.gaming && !nap.gaming);
        let mut saw_nap = false;
        let mut saw_game_after_nap = false;
        for _ in 0..3600 {
            game.update(1.0 / 30.0, true);
            saw_nap |= !game.gaming;
            saw_game_after_nap |= saw_nap && game.gaming;
        }
        assert!(saw_nap && saw_game_after_nap);
        game.update(0.03, false);
        assert!(!game.gaming && game.remaining == 0.0);
    }
}

//! Small, mutually exclusive comedy scenes. Simulation time is independent of
//! frame rate; cosmetic events never change application/audio state.
use super::{Surface, V};

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) enum Kind {
    Fire,
    Rain,
}

#[derive(Clone, Copy)]
pub(super) struct EventFrame {
    pub kind: Kind,
    pub age: f32,
    pub visibility: f32,
}

fn smooth(v: f32) -> f32 {
    let v = v.clamp(0.0, 1.0);
    v * v * (3.0 - 2.0 * v)
}

pub(super) const EXTINGUISHER_GRIP: V = [0.72, 0.62, -0.94];
const UMBRELLA_GRIP: V = [-0.30, 0.58, -0.86];

fn mix(a: V, b: V, t: f32) -> V {
    std::array::from_fn(|i| a[i] + (b[i] - a[i]) * t)
}

fn track(t: f32, keys: &[(f32, V)]) -> V {
    for pair in keys.windows(2) {
        if t < pair[1].0 {
            return mix(
                pair[0].1,
                pair[1].1,
                smooth((t - pair[0].0) / (pair[1].0 - pair[0].0)),
            );
        }
    }
    keys.last().unwrap().1
}

pub(super) struct Reaction {
    pub body: V,
    pub head: V,
    pub body_yaw: f32,
    pub body_pitch: f32,
    pub head_yaw: f32,
    pub head_pitch: f32,
    pub alarm: f32,
}

impl EventFrame {
    pub fn pose(self) -> f32 {
        let (up, down) = match self.kind {
            Kind::Fire => (0.6, 12.0),
            Kind::Rain => (1.6, 15.0),
        };
        smooth((self.age - up) / 0.35) * (1.0 - smooth((self.age - down) / 0.9)) * self.visibility
    }

    pub fn typing(self) -> bool {
        self.kind == Kind::Rain && self.age >= 5.8 && self.age < 12.6
    }

    fn umbrella_open(self) -> f32 {
        smooth((self.age - 5.0) / 0.8) * (1.0 - smooth((self.age - 13.0) / 0.8)) * self.visibility
    }

    fn canopy(self, hand: V, head: V) -> V {
        mix(
            [hand[0], hand[1] + 0.68, hand[2]],
            [head[0], head[1] + 1.04, head[2]],
            self.umbrella_open(),
        )
    }

    pub fn reaction(self) -> Reaction {
        let t = self.age;
        let (bend, flinch, yaw, pitch) = match self.kind {
            Kind::Fire => {
                let bend = track(
                    t,
                    &[
                        (0.0, [0.0; 3]),
                        (1.7, [0.0; 3]),
                        (2.8, [1.0; 3]),
                        (3.4, [1.0; 3]),
                        (4.6, [0.0; 3]),
                        (9.0, [0.0; 3]),
                        (10.4, [1.0; 3]),
                        (10.9, [1.0; 3]),
                        (12.0, [0.0; 3]),
                    ],
                )[0];
                let flinch = smooth((t - 0.6) / 0.2) * (1.0 - smooth((t - 1.2) / 0.5));
                (
                    bend,
                    flinch,
                    2.2 * bend + flinch * 1.85,
                    0.65 * bend - 0.30 * flinch,
                )
            }
            Kind::Rain => {
                let bend = track(
                    t,
                    &[
                        (0.0, [0.0; 3]),
                        (2.6, [0.0; 3]),
                        (3.6, [1.0; 3]),
                        (4.0, [1.0; 3]),
                        (5.3, [0.0; 3]),
                        (13.8, [0.0; 3]),
                        (14.8, [1.0; 3]),
                        (15.0, [1.0; 3]),
                        (15.8, [0.0; 3]),
                    ],
                )[0];
                let flinch = smooth((t - 1.65) / 0.25) * (1.0 - smooth((t - 2.6) / 0.5));
                (
                    bend,
                    flinch,
                    -1.9 * bend + 1.65 * flinch,
                    0.6 * bend - 0.65 * flinch,
                )
            }
        };
        let side = if self.kind == Kind::Fire { 1.0 } else { -1.0 };
        let shiver = flinch * (t * 32.0).sin() * 0.035;
        Reaction {
            body: [
                side * 0.12 * bend + shiver,
                -0.19 * bend + flinch * 0.065,
                -0.18 * bend - flinch * 0.10,
            ],
            head: [
                side * 0.24 * bend + shiver,
                -0.42 * bend + flinch * 0.04,
                -0.35 * bend - flinch * 0.18,
            ],
            body_yaw: side * 0.95 * bend + side * flinch * 0.20,
            body_pitch: -0.40 * bend - flinch * 0.16,
            head_yaw: yaw,
            head_pitch: pitch,
            alarm: flinch,
        }
    }

    pub fn hand(self, index: usize) -> Option<V> {
        let t = self.age;
        match (self.kind, index) {
            (Kind::Fire, 0) => Some(track(
                t,
                &[
                    (0.0, [-0.09, 1.15, -0.20]),
                    (1.0, [-0.22, 1.77, -0.56]),
                    (1.7, [-0.18, 1.55, -0.44]),
                    (2.8, [0.05, 0.96, -0.42]),
                    (3.4, [0.05, 0.96, -0.42]),
                    (4.6, [0.05, 1.70, -0.22]),
                    (8.9, [0.05, 1.70, -0.22]),
                    (10.4, [0.05, 0.96, -0.42]),
                    (10.9, [0.05, 0.96, -0.42]),
                    (12.0, [-0.09, 1.05, -0.14]),
                ],
            )),
            (Kind::Fire, _) => Some(track(
                t,
                &[
                    (0.0, [0.79, 1.15, -0.20]),
                    (1.0, [0.98, 1.79, -0.60]),
                    (1.7, [0.98, 1.48, -0.77]),
                    (2.8, EXTINGUISHER_GRIP),
                    (3.4, EXTINGUISHER_GRIP),
                    (4.6, [1.08, 1.46, -0.18]),
                    (8.9, [1.08, 1.46, -0.18]),
                    (10.4, EXTINGUISHER_GRIP),
                    (10.9, EXTINGUISHER_GRIP),
                    (12.0, [0.79, 1.05, -0.14]),
                ],
            )),
            (Kind::Rain, 0) => Some(track(
                t,
                &[
                    (0.0, [-0.09, 1.10, -0.14]),
                    (2.1, [-0.15, 1.91, -0.52]),
                    (2.6, [-0.25, 1.74, -0.58]),
                    (3.6, UMBRELLA_GRIP),
                    (4.0, UMBRELLA_GRIP),
                    (5.3, [-0.25, 1.69, -0.48]),
                    (13.8, [-0.25, 1.69, -0.48]),
                    (14.8, UMBRELLA_GRIP),
                    (15.0, UMBRELLA_GRIP),
                    (15.8, [-0.09, 1.05, -0.14]),
                ],
            )),
            (Kind::Rain, _) if !self.typing() => Some(track(
                t,
                &[
                    (0.0, [0.79, 1.10, -0.14]),
                    (2.1, [0.89, 1.95, -0.53]),
                    (3.6, [0.65, 1.83, -0.50]),
                    (5.4, [0.74, 1.78, -0.30]),
                    (5.8, [0.58, 1.45, 0.15]),
                    (12.6, [0.58, 1.45, 0.15]),
                    (13.8, [0.65, 1.74, -0.42]),
                    (14.8, [0.66, 1.0, -0.5]),
                    (15.8, [0.79, 1.05, -0.14]),
                ],
            )),
            _ => None,
        }
    }
}

pub(super) struct Events {
    rng: u64,
    wait: f32,
    active: Option<EventFrame>,
    cancel: Option<f32>,
}

impl Events {
    pub fn new(seed: u64) -> Self {
        let mut s = Self {
            rng: (seed ^ 0x9e3779b97f4a7c15).max(1),
            wait: 0.0,
            active: None,
            cancel: None,
        };
        s.wait = s.random(18.0, 35.0);
        s
    }

    fn random(&mut self, min: f32, max: f32) -> f32 {
        self.rng ^= self.rng >> 12;
        self.rng ^= self.rng << 25;
        self.rng ^= self.rng >> 27;
        let n = self.rng.wrapping_mul(0x2545F4914F6CDD1D);
        min + (max - min) * ((n >> 40) as f32 / 16_777_216.0)
    }

    pub fn trigger(&mut self, kind: Kind) -> bool {
        if self.active.is_some() {
            return false;
        }
        self.active = Some(EventFrame {
            kind,
            age: 0.0,
            visibility: 1.0,
        });
        self.cancel = None;
        true
    }

    pub fn frame(&self) -> Option<EventFrame> {
        self.active
    }

    pub fn update(&mut self, dt: f32, can_start: bool, interrupt: bool) {
        if let Some(mut e) = self.active {
            e.age += dt;
            if interrupt && self.cancel.is_none() {
                self.cancel = Some(0.0);
            }
            if let Some(elapsed) = &mut self.cancel {
                *elapsed += dt;
                e.visibility = 1.0 - smooth(*elapsed / 0.45);
            }
            let duration = match e.kind {
                Kind::Fire => 13.0,
                Kind::Rain => 16.0,
            };
            if e.age >= duration || e.visibility <= 0.0 {
                self.active = None;
                self.cancel = None;
                self.wait = self.random(40.0, 90.0);
            } else {
                self.active = Some(e);
            }
        } else if can_start && !interrupt {
            self.wait -= dt;
            if self.wait <= 0.0 {
                let kind = if self.random(0.0, 1.0) < 0.5 {
                    Kind::Fire
                } else {
                    Kind::Rain
                };
                self.trigger(kind);
            }
        }
    }
}

fn rod(s: &mut Surface, a: V, b: V, width: f32, color: [u8; 3]) {
    let d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let horizontal = d[0].hypot(d[2]);
    s.cube(
        [
            (a[0] + b[0]) * 0.5,
            (a[1] + b[1]) * 0.5,
            (a[2] + b[2]) * 0.5,
        ],
        [width, width, horizontal.hypot(d[1])],
        d[0].atan2(d[2]),
        -d[1].atan2(horizontal),
        color,
    );
}

pub(super) fn draw(s: &mut Surface, event: Option<EventFrame>, hands: [V; 2], head: V) {
    let grip = event
        .filter(|e| e.kind == Kind::Fire && e.age >= 3.05 && e.age < 10.65)
        .map_or(EXTINGUISHER_GRIP, |e| {
            mix(EXTINGUISHER_GRIP, hands[1], e.visibility)
        });
    // A persistent, full-size prop under the seat; it never scales into existence.
    s.block(
        [grip[0], grip[1] - 0.275, grip[2]],
        [0.32, 0.49, 0.29],
        [232, 47, 40],
    );
    s.block(
        [grip[0], grip[1] - 0.025, grip[2]],
        [0.20, 0.08, 0.18],
        [35, 45, 53],
    );
    rod(
        s,
        [grip[0] - 0.1, grip[1] + 0.01, grip[2]],
        [grip[0] + 0.1, grip[1] + 0.01, grip[2]],
        0.06,
        [39, 49, 58],
    );
    s.block(
        [grip[0] + 0.163, grip[1] - 0.27, grip[2]],
        [0.008, 0.20, 0.19],
        [244, 238, 206],
    );
    s.block(
        [grip[0], grip[1] - 0.27, grip[2] - 0.148],
        [0.19, 0.20, 0.008],
        [244, 238, 206],
    );
    if !event.is_some_and(|e| e.kind == Kind::Rain && e.age >= 3.8 && e.age < 14.95) {
        folded_umbrella(s, UMBRELLA_GRIP);
    }
    if let Some(event) = event {
        match event.kind {
            Kind::Fire => fire(s, event, hands),
            Kind::Rain => rain(s, event, hands[0], head),
        }
    }
}

fn folded_umbrella(s: &mut Surface, grip: V) {
    rod(
        s,
        grip,
        [grip[0], grip[1] + 0.68, grip[2]],
        0.045,
        [168, 187, 199],
    );
    s.cube(
        [grip[0], grip[1] + 0.38, grip[2]],
        [0.12, 0.58, 0.12],
        0.3,
        0.0,
        [235, 177, 58],
    );
    s.block(
        [grip[0], grip[1] - 0.03, grip[2]],
        [0.09, 0.16, 0.10],
        [106, 73, 46],
    );
}

fn fire(s: &mut Surface, e: EventFrame, hands: [V; 2]) {
    let t = e.age;
    let intensity = smooth((t - 0.35) / 0.65) * (1.0 - smooth((t - 5.4) / 2.7)) * e.visibility;
    // Voxel tongues rise from the laptop, then shrink as foam reaches them.
    for i in 0..9 {
        let phase = i as f32 * 1.71;
        let x = 0.48 + phase.sin() * 0.29;
        let z = 0.57 + phase.cos() * 0.19;
        let h = (0.55 + 0.40 * (t * 10.0 + phase).sin().abs()) * intensity;
        if h > 0.005 {
            let r = 0.13 * intensity;
            let base = [
                [x - r, 1.40, z - r],
                [x + r, 1.40, z - r],
                [x + r, 1.40, z + r],
                [x - r, 1.40, z + r],
            ];
            let tip = [
                x + (t * 8.0 + phase).sin() * 0.13 * intensity,
                1.40 + h,
                z + (t * 6.0 + phase).cos() * 0.06 * intensity,
            ];
            for face in 0..4 {
                let color = [
                    [255, 174, 43],
                    [255, 224, 95],
                    [239, 83, 24],
                    [249, 116, 26],
                ][face];
                s.triangle(base[face], base[(face + 1) % 4], tip, color);
            }
        }
    }
    let smoke = smooth(t / 0.6) * (1.0 - smooth((t - 7.0) / 2.5)) * e.visibility;
    for i in 0..7 {
        let p = (t * 0.5 + i as f32 * 0.143).fract();
        let size = (0.09 + p * 0.22) * smoke;
        if size > 0.005 {
            s.cube(
                [
                    0.35 + (p * 6.0 + i as f32).sin() * 0.14,
                    1.75 + p * 0.95,
                    0.56 + p * 0.10,
                ],
                [size; 3],
                p,
                p * 0.4,
                [101, 113, 123].map(|c| (c as f32 * (1.0 - p * 0.55)) as u8),
            );
        }
    }
    let pose = e.pose();
    if pose > 0.01 && t >= 3.4 && t < 10.4 {
        let right = hands[1];
        // Compact handle-mounted nozzle: the jet is on the camera-facing side,
        // clear of the head. The free hand shields the robot from the flames.
        let hose_mid = [right[0] + 0.18, right[1] - 0.15, right[2] + 0.06];
        let nozzle = [right[0] - 0.06, right[1] + 0.08, right[2] + 0.23];
        rod(s, right, hose_mid, 0.055 * pose, [36, 48, 59]);
        rod(s, hose_mid, nozzle, 0.055 * pose, [36, 48, 59]);
        rod(s, right, nozzle, 0.10 * pose, [66, 79, 90]);
        let spraying = smooth((t - 4.8) / 0.25) * (1.0 - smooth((t - 8.4) / 0.5)) * pose;
        if spraying > 0.01 {
            let impact = [0.48 + (t * 6.0).sin() * 0.10, 1.83, 0.58];
            rod(
                s,
                nozzle,
                mix(nozzle, impact, 0.76),
                0.075 * spraying,
                [224, 250, 255],
            );
            for i in 0..48 {
                let p = (t * 3.8 + i as f32 * 0.137).fract();
                let spread = p * 0.23;
                let angle = i as f32 * 2.4;
                let size = (0.05 + p * 0.075) * spraying;
                let center = mix(nozzle, impact, p);
                s.block(
                    [
                        center[0] + angle.sin() * spread,
                        center[1] + angle.cos() * spread,
                        center[2],
                    ],
                    [size; 3],
                    [219, 241, 248],
                );
            }
        }
    }
    let foam = smooth((t - 5.3) / 1.5) * (1.0 - smooth((t - 10.5) / 1.8)) * e.visibility;
    if foam > 0.01 {
        for i in 0..9 {
            s.block(
                [
                    0.1 + (i % 3) as f32 * 0.19,
                    1.40,
                    0.20 + (i / 3) as f32 * 0.15,
                ],
                [0.17 * foam, 0.035 * foam, 0.12 * foam],
                [207, 232, 239],
            );
        }
    }
}

fn rain(s: &mut Surface, e: EventFrame, hand: V, head: V) {
    let t = e.age;
    let arrive = smooth(t / 1.8);
    let leave = smooth((t - 13.0) / 3.0);
    let cloud_x = -3.7 * (1.0 - arrive) + 3.7 * leave;
    let cloud_scale = smooth(t / 0.7) * (1.0 - smooth((t - 15.0) / 1.0)) * e.visibility;
    for i in 0..6 {
        let x = cloud_x - 0.65 + (i % 3) as f32 * 0.55;
        let z = -0.12 + (i / 3) as f32 * 0.42;
        let y = 3.42 + if i % 3 == 1 { 0.16 } else { 0.0 };
        s.block(
            [x, y, z],
            [0.69 * cloud_scale, 0.35 * cloud_scale, 0.58 * cloud_scale],
            [166, 187, 204],
        );
    }
    let open = e.umbrella_open();
    // The left hand carries the pole; the right hand remains free to type.
    let canopy = e.canopy(hand, head);
    if t >= 3.8 && t < 14.95 {
        let radius = 0.06 + 0.93 * open;
        let top = [canopy[0], canopy[1], canopy[2]];
        // The shaft rises outside the head, then bends inward above the crown.
        // The canopy itself follows the actual head center, including ducking.
        let bend = mix(
            [hand[0], hand[1] + 0.48, hand[2]],
            [hand[0], head[1] + 0.51, hand[2]],
            open,
        );
        rod(s, hand, bend, 0.045, [192, 208, 212]);
        rod(s, bend, top, 0.045, [192, 208, 212]);
        s.block(
            [hand[0], hand[1] - 0.08, hand[2]],
            [0.11, 0.20, 0.11],
            [117, 78, 47],
        );
        for i in 0..8 {
            let a = i as f32 * std::f32::consts::TAU / 8.0;
            let b = (i + 1) as f32 * std::f32::consts::TAU / 8.0;
            let point = |angle: f32, r: f32, drop: f32| {
                [
                    canopy[0] + angle.cos() * r,
                    canopy[1] - drop * open - 0.45 * (1.0 - open),
                    canopy[2] + angle.sin() * r,
                ]
            };
            let ma = point(a, radius * 0.53, 0.12);
            let mb = point(b, radius * 0.53, 0.12);
            let ra = point(a, radius, 0.35);
            let rb = point(b, radius, 0.35);
            let color = if i % 2 == 0 {
                [247, 194, 74]
            } else {
                [225, 144, 47]
            };
            s.triangle(top, ma, mb, color);
            s.quad([ma, ra, rb, mb], color);
            rod(s, top, ma, 0.018, [255, 221, 125]);
            rod(s, ma, ra, 0.018, [255, 221, 125]);
        }
    }
    let raining = smooth((t - 1.7) / 0.5) * (1.0 - smooth((t - 12.4) / 0.6)) * e.visibility;
    if raining > 0.01 {
        for i in 0..54 {
            let x = -1.20 + (i as f32 * 0.618).fract() * 2.5;
            let z = -1.05 + (i as f32 * 0.381).fract() * 2.2;
            let under_umbrella = open > 0.6 && (x - canopy[0]).hypot(z - canopy[2]) < 0.96 * open;
            let floor = if under_umbrella {
                canopy[1] - 0.32
            } else if z > -0.09 {
                1.33
            } else {
                0.10
            };
            let p = (t * 1.5 + i as f32 * 0.137).fract();
            let y = 3.17 - p * (3.17 - floor);
            s.block(
                [x, y, z],
                [0.018 * raining, 0.13 * raining, 0.018 * raining],
                [125, 196, 246],
            );
            if p > 0.91 {
                s.block(
                    [x, floor + 0.012, z],
                    [0.10 * raining, 0.012, 0.08 * raining],
                    [108, 167, 190],
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn events_end_and_cannot_overlap() {
        for kind in [Kind::Fire, Kind::Rain] {
            let mut s = Events::new(7);
            assert!(s.trigger(kind));
            assert!(!s.trigger(kind));
            for _ in 0..(18 * 30) {
                s.update(1.0 / 30.0, false, false);
            }
            assert!(s.frame().is_none());
            assert!(s.wait >= 40.0 && s.wait <= 90.0);
        }
    }
    #[test]
    fn blocked_states_pause_schedule_and_cancel_smoothly() {
        let mut s = Events::new(7);
        for _ in 0..5000 {
            s.update(1.0 / 30.0, false, false);
        }
        assert!(s.frame().is_none());
        s.trigger(Kind::Rain);
        s.update(0.1, false, true);
        assert!(s.frame().unwrap().visibility > 0.5);
        for _ in 0..15 {
            s.update(1.0 / 30.0, false, true);
        }
        assert!(s.frame().is_none());
    }

    #[test]
    fn retrieval_targets_are_reachable_with_fixed_length_arms() {
        for (kind, age, index, target) in [
            (Kind::Fire, 3.2, 1, EXTINGUISHER_GRIP),
            (Kind::Rain, 3.9, 0, UMBRELLA_GRIP),
        ] {
            let e = EventFrame {
                kind,
                age,
                visibility: 1.0,
            };
            let r = e.reaction();
            let body = [0.35 + r.body[0], 1.1 + r.body[1], -0.51 + r.body[2]];
            let shoulder = super::super::transform(
                [if index == 0 { -0.44 } else { 0.44 }, 0.26, 0.01],
                body,
                r.body_yaw,
                r.body_pitch,
            );
            let (_, hand) = super::super::arm_pose(
                shoulder,
                e.hand(index).unwrap(),
                if index == 0 { -1.0 } else { 1.0 },
            );
            for c in 0..3 {
                assert!((hand[c] - target[c]).abs() < 0.015);
            }
            assert!(r.head_pitch > 0.5 && r.head_yaw.abs() > 1.8);
        }
    }

    #[test]
    fn open_umbrella_tracks_head_not_the_holding_hand() {
        let event = EventFrame {
            kind: Kind::Rain,
            age: 7.0,
            visibility: 1.0,
        };
        for head in [[0.35, 1.90, -0.48], [0.20, 1.75, -0.65]] {
            let canopy = event.canopy([-0.25, 1.69, -0.48], head);
            assert!((canopy[0] - head[0]).abs() < 0.001);
            assert!((canopy[2] - head[2]).abs() < 0.001);
            assert!(canopy[1] > head[1] + 0.9);
        }
    }
    #[test]
    fn seed_repeats_but_events_include_both_kinds() {
        let mut a = Events::new(42);
        let mut b = Events::new(42);
        let (mut fire, mut rain) = (false, false);
        for _ in 0..(1200 * 20) {
            a.update(0.05, true, false);
            b.update(0.05, true, false);
            assert_eq!(a.frame().map(|e| e.kind), b.frame().map(|e| e.kind));
            if let Some(e) = a.frame() {
                fire |= e.kind == Kind::Fire;
                rain |= e.kind == Kind::Rain;
            }
        }
        assert!(fire && rain);
    }
}

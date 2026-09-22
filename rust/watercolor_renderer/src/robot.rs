//! Small orthographic voxel diorama. No GPU, assets, or per-frame Python geometry.
//! Static geometry/depth is cached; moving meshes share its z-buffer. 2x
//! supersampling keeps slow head rotations smooth even on a 240px LCD.
use crate::robot_gestures::{IdleGestures, IdlePastime, WorkGestures};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

#[path = "robot_events.rs"]
mod events;
use events::{Events, Kind};

type V = [f32; 3];
type Color = [u8; 3];

#[derive(Clone)]
struct Surface {
    width: usize,
    height: usize,
    pixels: Vec<Color>,
    depth: Vec<f32>,
    scale: f32,
    orbit: (f32, f32),
}

impl Surface {
    fn new(width: usize, height: usize) -> Self {
        Self {
            width: width * 2,
            height: height * 2,
            pixels: vec![[0; 3]; width * height * 4],
            depth: vec![f32::NEG_INFINITY; width * height * 4],
            scale: (width as f32 / 240.0).min(height as f32 / 280.0) * 64.0,
            orbit: (0.0, 1.0),
        }
    }

    fn project(&self, p: V) -> V {
        let (sin, cos) = self.orbit;
        let p = [cos * p[0] - sin * p[2], p[1], sin * p[0] + cos * p[2]];
        [
            // Camera over the robot's right shoulder: +X, -Z, looking toward +Z.
            self.width as f32 * 0.5 + (p[0] + p[2]) * 0.7071 * self.scale,
            self.height as f32 * 0.60 + ((p[0] - p[2]) * 0.34 - p[1] * 0.88) * self.scale,
            (p[0] - p[2]) * 0.622 + p[1] * 0.48,
        ]
    }

    fn triangle(&mut self, a: V, b: V, c: V, color: Color) {
        let a = self.project(a);
        let b = self.project(b);
        let c = self.project(c);
        let edge =
            |a: V, b: V, x: f32, y: f32| (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
        let area = edge(a, b, c[0], c[1]);
        if area.abs() < 0.0001 {
            return;
        }
        let x0 = a[0].min(b[0]).min(c[0]).floor().max(0.0) as usize;
        let x1 = a[0].max(b[0]).max(c[0]).ceil().min(self.width as f32) as usize;
        let y0 = a[1].min(b[1]).min(c[1]).floor().max(0.0) as usize;
        let y1 = a[1].max(b[1]).max(c[1]).ceil().min(self.height as f32) as usize;
        // Increment edge functions across the scanline instead of doing three
        // divisions and six edge multiplications per pixel (important on Zero 2).
        let inverse_area = area.recip();
        let du = (b[1] - c[1]) * inverse_area;
        let dv = (c[1] - a[1]) * inverse_area;
        let depth_u = a[2] - c[2];
        let depth_v = b[2] - c[2];
        for y in y0..y1 {
            let mut u = edge(b, c, x0 as f32 + 0.5, y as f32 + 0.5) * inverse_area;
            let mut v = edge(c, a, x0 as f32 + 0.5, y as f32 + 0.5) * inverse_area;
            for x in x0..x1 {
                let w = 1.0 - u - v;
                if u >= -0.00001 && v >= -0.00001 && w >= -0.00001 {
                    let z = c[2] + depth_u * u + depth_v * v;
                    let i = y * self.width + x;
                    if z > self.depth[i] {
                        self.depth[i] = z;
                        self.pixels[i] = color;
                    }
                }
                u += du;
                v += dv;
            }
        }
    }

    fn quad(&mut self, p: [V; 4], color: Color) {
        self.triangle(p[0], p[1], p[2], color);
        self.triangle(p[0], p[2], p[3], color);
    }

    fn cube(&mut self, center: V, size: V, yaw: f32, pitch: f32, color: Color) {
        let mut p = [[0.0; 3]; 8];
        for (i, vertex) in p.iter_mut().enumerate() {
            let local = [
                size[0] * (if i & 1 == 0 { -0.5 } else { 0.5 }),
                size[1] * (if i & 2 == 0 { -0.5 } else { 0.5 }),
                size[2] * (if i & 4 == 0 { -0.5 } else { 0.5 }),
            ];
            *vertex = transform(local, center, yaw, pitch);
        }
        for (indices, light) in [
            ([0, 1, 3, 2], 0.65),
            ([4, 6, 7, 5], 0.88),
            ([0, 2, 6, 4], 0.72),
            ([1, 5, 7, 3], 0.78),
            ([0, 4, 5, 1], 0.55),
            ([2, 3, 7, 6], 1.0),
        ] {
            let face = indices.map(|i| p[i]);
            let [a, b, c] = [
                self.project(face[0]),
                self.project(face[1]),
                self.project(face[2]),
            ];
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0.0 {
                self.quad(face, color.map(|c| (c as f32 * light) as u8));
            }
        }
    }

    fn block(&mut self, center: V, size: V, color: Color) {
        self.cube(center, size, 0.0, 0.0, color);
    }

    /// Thin circular speaker on either local X side of the head. All vertices,
    /// including the grille, share the head transform and scene depth buffer.
    fn speaker_ear(&mut self, side: f32, head: V, yaw: f32, pitch: f32) {
        let point = |x: f32, y: f32, z: f32| transform([side * x, y, z], head, yaw, pitch);
        let ring =
            |x: f32, radius: f32, angle: f32| point(x, radius * angle.sin(), radius * angle.cos());
        let center = point(0.537, 0.0, 0.0);
        for i in 0..24 {
            let a = i as f32 * std::f32::consts::TAU / 24.0;
            let b = (i + 1) as f32 * std::f32::consts::TAU / 24.0;
            let light = 0.76 + 0.18 * ((a + b) * 0.5).sin();
            self.quad(
                [
                    ring(0.438, 0.225, a),
                    ring(0.438, 0.225, b),
                    ring(0.535, 0.225, b),
                    ring(0.535, 0.225, a),
                ],
                [182, 207, 218].map(|c| (c as f32 * light) as u8),
            );
            self.quad(
                [
                    ring(0.535, 0.225, a),
                    ring(0.535, 0.225, b),
                    ring(0.537, 0.170, b),
                    ring(0.537, 0.170, a),
                ],
                [187, 215, 228],
            );
            self.triangle(
                center,
                ring(0.537, 0.170, a),
                ring(0.537, 0.170, b),
                [48, 73, 89],
            );
        }
        // Small recessed grille holes stay visible at LCD resolution without
        // a texture lookup or extra image assets.
        for row in -2..=2 {
            for col in -2..=2 {
                if row * row + col * col > 5 {
                    continue;
                }
                let y = row as f32 * 0.06;
                let z = col as f32 * 0.06;
                let r = 0.018;
                self.quad(
                    [
                        point(0.539, y - r, z - r),
                        point(0.539, y + r, z - r),
                        point(0.539, y + r, z + r),
                        point(0.539, y - r, z + r),
                    ],
                    [15, 30, 42],
                );
            }
        }
    }

    fn limb(&mut self, from: V, to: V, color: Color) {
        let d = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
        let horizontal = d[0].hypot(d[2]);
        self.cube(
            [
                (from[0] + to[0]) * 0.5,
                (from[1] + to[1]) * 0.5,
                (from[2] + to[2]) * 0.5,
            ],
            [0.18, 0.18, horizontal.hypot(d[1]) + 0.08],
            d[0].atan2(d[2]),
            -d[1].atan2(horizontal),
            color,
        );
    }
}

fn transform(p: V, center: V, yaw: f32, pitch: f32) -> V {
    let (sp, cp) = pitch.sin_cos();
    let (sy, cy) = yaw.sin_cos();
    let y = p[1] * cp - p[2] * sp;
    let z = p[1] * sp + p[2] * cp;
    [
        center[0] + p[0] * cy + z * sy,
        center[1] + y,
        center[2] - p[0] * sy + z * cy,
    ]
}

/// Two fixed-length arm segments; unreachable targets are clamped rather than
/// stretching the forearm. Bend elbows outward, slightly downward and back.
#[cfg(test)]
fn arm_pose(shoulder: V, target: V, side: f32) -> (V, V) {
    arm_pose_lift(shoulder, target, side, 0.0)
}

fn arm_pose_lift(shoulder: V, target: V, side: f32, lift: f32) -> (V, V) {
    let d = std::array::from_fn::<_, 3, _>(|i| target[i] - shoulder[i]);
    let length = d.iter().map(|x| x * x).sum::<f32>().sqrt().max(0.0001);
    let axis = d.map(|x| x / length);
    let reach = length.clamp(0.03, 0.739);
    let hand = std::array::from_fn(|i| shoulder[i] + axis[i] * reach);
    let preferred = [side, -0.5 + lift * 1.8, -0.2];
    let dot = (0..3).map(|i| preferred[i] * axis[i]).sum::<f32>();
    let bend = std::array::from_fn::<_, 3, _>(|i| preferred[i] - dot * axis[i]);
    let norm = bend.iter().map(|x| x * x).sum::<f32>().sqrt().max(0.0001);
    let along = (0.36_f32.powi(2) - 0.38_f32.powi(2) + reach * reach) / (2.0 * reach);
    let height = (0.36_f32.powi(2) - along * along).max(0.0).sqrt();
    let elbow = std::array::from_fn(|i| shoulder[i] + axis[i] * along + bend[i] / norm * height);
    (elbow, hand)
}

fn static_scene(s: &mut Surface) {
    // One cut-out map tile, with exposed soil strata and a tiled office floor.
    s.block([0.0, -0.20, 0.0], [3.7, 0.35, 3.15], [82, 69, 62]);
    s.block([0.0, -0.035, 0.0], [3.76, 0.10, 3.21], [132, 147, 129]);
    for x in 0..6 {
        for z in 0..5 {
            let c = if (x + z) % 2 == 0 {
                [169, 184, 163]
            } else {
                [156, 173, 153]
            };
            s.block(
                [-1.54 + x as f32 * 0.615, 0.03, -1.255 + z as f32 * 0.625],
                [0.60, 0.055, 0.61],
                c,
            );
        }
    }
    // Inset rug, chair base, stem, seat and low backrest.
    s.block([0.35, 0.07, -0.65], [1.65, 0.025, 1.38], [106, 130, 130]);
    s.block([0.35, 0.14, -0.65], [1.0, 0.09, 0.16], [51, 65, 74]);
    s.block([0.35, 0.14, -0.65], [0.16, 0.09, 0.94], [51, 65, 74]);
    s.block([0.35, 0.40, -0.65], [0.14, 0.52, 0.14], [104, 122, 131]);
    s.block([0.35, 0.70, -0.65], [0.90, 0.16, 0.80], [48, 75, 91]);
    s.block([0.35, 1.06, -1.0], [0.89, 0.66, 0.13], [58, 88, 106]);
    // Desk: four slender metal legs, warm wooden top and bevel-like edging.
    for x in [-1.24, 1.23] {
        for z in [0.13, 1.12] {
            s.block([x, 0.59, z], [0.12, 1.1, 0.12], [64, 77, 86]);
        }
    }
    s.block([0.0, 1.19, 0.62], [2.86, 0.17, 1.38], [180, 128, 83]);
    s.block([0.0, 1.285, 0.62], [2.90, 0.04, 1.42], [226, 180, 126]);
    // Keyboard near the robot, hinge at the far edge, display facing -Z.
    s.block([0.35, 1.335, 0.42], [1.04, 0.065, 0.88], [113, 138, 155]);
    s.block([0.35, 1.373, 0.25], [0.86, 0.012, 0.36], [33, 51, 65]);
    for row in 0..3 {
        for col in 0..8 {
            s.block(
                [0.0 + col as f32 * 0.10, 1.382, 0.14 + row as f32 * 0.10],
                [0.07, 0.012, 0.06],
                [130, 163, 180],
            );
        }
    }
    s.block([0.35, 1.72, 0.85], [1.06, 0.72, 0.055], [91, 117, 140]);
    s.block([0.35, 1.72, 0.818], [0.94, 0.59, 0.014], [10, 25, 35]);
    // The mug is dynamic so a work break can lift it without leaving a duplicate.
    s.block([-1.11, 1.44, 0.98], [0.31, 0.27, 0.31], [180, 101, 78]);
    s.block([-1.11, 1.71, 0.98], [0.10, 0.38, 0.10], [64, 112, 77]);
    s.block([-1.20, 1.80, 0.98], [0.29, 0.20, 0.21], [100, 159, 101]);
    s.block([-1.02, 1.93, 0.98], [0.24, 0.22, 0.24], [124, 180, 112]);
}

fn drink_hand(age: f32) -> V {
    let keys = [
        (0.0, [0.58, 1.45, 0.15]),
        (0.7, [0.87, 1.60, -0.02]),
        (1.4, [0.90, 1.46, 0.15]),
        (2.0, [0.80, 1.65, 0.10]),
        (2.8, [0.70, 1.80, -0.10]),
        (3.8, [0.70, 1.80, -0.10]),
        (4.7, [0.80, 1.65, 0.10]),
        (5.8, [0.90, 1.46, 0.15]),
        (6.4, [0.87, 1.60, -0.02]),
        (7.0, [0.58, 1.45, 0.15]),
    ];
    for pair in keys.windows(2) {
        if age <= pair[1].0 {
            let u = ((age - pair[0].0) / (pair[1].0 - pair[0].0)).clamp(0.0, 1.0);
            let u = u * u * (3.0 - 2.0 * u);
            return std::array::from_fn(|i| pair[0].1[i] + (pair[1].1[i] - pair[0].1[i]) * u);
        }
    }
    keys.last().unwrap().1
}

#[pyclass]
pub struct RobotRenderer {
    background: Surface,
    frame: Surface,
    sleep: f32,
    work: f32,
    think: f32,
    gestures: IdleGestures,
    work_gestures: WorkGestures,
    pastime: IdlePastime,
    game: f32,
    events: Events,
    effects_allowed: bool,
    previous_time: Option<f32>,
    orbit: f32,
    orbit_start: f32,
    orbit_elapsed: f32,
}

#[pymethods]
impl RobotRenderer {
    #[new]
    #[pyo3(signature = (width=240, height=280, seed=None))]
    fn new(width: usize, height: usize, seed: Option<u64>) -> PyResult<Self> {
        if !(64..=1024).contains(&width) || !(64..=1024).contains(&height) {
            return Err(PyValueError::new_err(
                "width and height must be between 64 and 1024",
            ));
        }
        let mut background = Surface::new(width, height);
        static_scene(&mut background);
        let seed = seed.unwrap_or_else(|| {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(1, |d| d.as_nanos() as u64)
        });
        Ok(Self {
            frame: background.clone(),
            background,
            sleep: 0.0,
            work: 0.0,
            think: 0.0,
            gestures: IdleGestures::new(seed),
            work_gestures: WorkGestures::new(seed ^ 0x92d68ca2),
            pastime: IdlePastime::new(seed),
            game: 0.0,
            events: Events::new(seed),
            effects_allowed: true,
            previous_time: None,
            orbit: 0.0,
            orbit_start: 0.0,
            orbit_elapsed: 0.9,
        })
    }

    /// One eased quarter-turn around the ground normal; ignore repeats mid-turn.
    fn rotate_view(&mut self) {
        if self.orbit_elapsed >= 0.9 {
            self.orbit_start = self.orbit;
            self.orbit_elapsed = 0.0;
        }
    }

    /// Preview hook; ordinary work breaks are scheduled with seeded random gaps.
    fn trigger_work_break(&mut self, name: &str) -> PyResult<()> {
        let kind = match name {
            "think" => 1,
            "drink" => 2,
            _ => return Err(PyValueError::new_err("work break must be think or drink")),
        };
        if self.work_gestures.kind != 0 {
            return Err(PyValueError::new_err("a work break is already active"));
        }
        self.work_gestures.kind = kind;
        self.work_gestures.age = 0.0;
        Ok(())
    }

    /// Deterministic preview/debug hook; normal playback schedules events itself.
    fn trigger_event(&mut self, name: &str) -> PyResult<()> {
        let kind = match name {
            "fire" => Kind::Fire,
            "rain" => Kind::Rain,
            _ => return Err(PyValueError::new_err("event must be fire or rain")),
        };
        if !self.events.trigger(kind) {
            return Err(PyValueError::new_err("an event is already active"));
        }
        Ok(())
    }

    #[pyo3(signature = (time, working, idle_seconds, sleep_after=45.0, overlay=None, thinking=false, effects_allowed=true))]
    fn rgb565<'py>(
        &mut self,
        py: Python<'py>,
        time: f32,
        working: bool,
        idle_seconds: f32,
        sleep_after: f32,
        overlay: Option<&[u8]>,
        thinking: bool,
        effects_allowed: bool,
    ) -> PyResult<Bound<'py, PyBytes>> {
        if !time.is_finite()
            || !idle_seconds.is_finite()
            || !sleep_after.is_finite()
            || time < 0.0
            || idle_seconds < 0.0
            || sleep_after <= 0.0
        {
            return Err(PyValueError::new_err(
                "animation times must be finite and nonnegative; sleep_after must be positive",
            ));
        }
        if overlay.is_some_and(|o| o.len() != self.frame.width * self.frame.height) {
            return Err(PyValueError::new_err(
                "overlay must be width * height * 4 RGBA bytes",
            ));
        }
        self.effects_allowed = effects_allowed;
        let bytes = py.allow_threads(|| {
            self.render(time, working, idle_seconds, sleep_after, overlay, thinking)
        });
        Ok(PyBytes::new_bound(py, &bytes))
    }
}

impl RobotRenderer {
    fn render(
        &mut self,
        t: f32,
        working: bool,
        idle: f32,
        sleep_after: f32,
        overlay: Option<&[u8]>,
        thinking: bool,
    ) -> Vec<u8> {
        let dt = self
            .previous_time
            .map_or(0.0, |last| (t - last).clamp(0.0, 0.1));
        self.previous_time = Some(t);
        if self.orbit_elapsed < 0.9 {
            self.orbit_elapsed = (self.orbit_elapsed + dt).min(0.9);
            let u = self.orbit_elapsed / 0.9;
            self.orbit = self.orbit_start + std::f32::consts::FRAC_PI_2 * u * u * (3.0 - 2.0 * u);
            if self.orbit_elapsed >= 0.9 {
                self.orbit = self.orbit.rem_euclid(std::f32::consts::TAU);
            }
            self.background.orbit = self.orbit.sin_cos();
            self.background.pixels.fill([0; 3]);
            self.background.depth.fill(f32::NEG_INFINITY);
            static_scene(&mut self.background);
            self.frame.orbit = self.background.orbit;
        }
        let wants_sleep = !working && !thinking && idle >= sleep_after;
        self.events.update(
            dt,
            !wants_sleep && !thinking && self.effects_allowed && self.sleep < 0.05,
            thinking || !self.effects_allowed,
        );
        let event = self.events.frame();
        let pose = event.map_or(0.0, |e| e.pose());
        let busy_pose = event.filter(|e| !e.typing()).map_or(0.0, |_| pose);
        let rain_work = event.is_some_and(|e| e.typing());
        let reaction = event.map(|e| e.reaction());
        self.pastime.update(dt, wants_sleep && event.is_none());
        let gaming = self.pastime.gaming;
        self.game += ((gaming as u8 as f32) - self.game) * (1.0 - (-dt * 6.0).exp());
        let asleep = wants_sleep && event.is_none() && !gaming;
        self.work_gestures.update(
            dt,
            working && !thinking && event.is_none() && self.sleep < 0.05,
        );
        self.sleep += ((asleep as u8 as f32) - self.sleep) * (1.0 - (-dt * 3.0).exp());
        self.work += ((((working || rain_work || gaming) && !thinking) as u8 as f32) - self.work)
            * (1.0 - (-dt * 7.0).exp());
        self.think += ((thinking as u8 as f32) - self.think) * (1.0 - (-dt * 5.0).exp());
        self.gestures.update(
            dt,
            !working && !thinking && !wants_sleep && self.sleep < 0.05 && event.is_none(),
        );
        self.frame.pixels.copy_from_slice(&self.background.pixels);
        self.frame.depth.copy_from_slice(&self.background.depth);
        let s = &mut self.frame;
        let sleep = self.sleep;
        let stretch = self.gestures.stretch * (1.0 - sleep) * (1.0 - pose);
        let extra = self.gestures.extra * (1.0 - sleep) * (1.0 - pose);
        let wave = if self.gestures.extra_kind == 1 {
            extra
        } else {
            0.0
        };
        let watch = if self.gestures.extra_kind == 2 {
            extra
        } else {
            0.0
        };
        let routine = self.work_gestures.weight * (1.0 - pose) * (1.0 - sleep);
        let drink = if self.work_gestures.kind == 2 {
            routine
        } else {
            0.0
        };
        let work = self.work * (1.0 - sleep) * (1.0 - busy_pose) * (1.0 - routine);
        let think = self.think.max(if self.work_gestures.kind == 1 {
            routine
        } else {
            0.0
        }) * (1.0 - sleep)
            * (1.0 - pose);
        let breath = (t * 2.2).sin() * 0.012;
        // Lower legs and feet remain anchored to the seat as the body leans.
        for (i, x) in [0.12, 0.58].iter().enumerate() {
            let angle = self.gestures.feet[i] * (1.0 - sleep) * (1.0 - pose);
            let knee = [*x, 0.82, -0.23];
            let ankle = [*x, 0.82 - 0.42 * angle.cos(), -0.23 + 0.42 * angle.sin()];
            s.limb(knee, ankle, [158, 185, 197]);
            s.cube(
                [*x, ankle[1], ankle[2] + 0.11],
                [0.27, 0.16, 0.40],
                0.0,
                -angle,
                [209, 228, 232],
            );
        }
        // Keep the torso behind the desk edge throughout the lean, never inside it.
        let mut body = [
            0.35,
            1.10 + sleep * 0.10 + breath + stretch * 0.06,
            -0.51 - sleep * 0.04,
        ];
        let body_yaw = reaction.as_ref().map_or(0.0, |r| r.body_yaw * pose);
        let body_pitch =
            sleep * 0.62 - stretch * 0.18 + reaction.as_ref().map_or(0.0, |r| r.body_pitch * pose);
        if let Some(r) = &reaction {
            for c in 0..3 {
                body[c] += r.body[c] * pose;
            }
        }
        s.cube(
            body,
            [0.66, 0.66, 0.43],
            body_yaw,
            body_pitch,
            [199, 219, 224],
        );
        s.cube(
            transform([0.0, 0.16, 0.232], body, body_yaw, body_pitch),
            [0.15, 0.08, 0.014],
            body_yaw,
            body_pitch,
            [71, 175, 225],
        );
        let camera_heading = (std::f32::consts::PI * 0.75 + self.orbit + std::f32::consts::PI)
            .rem_euclid(std::f32::consts::TAU)
            - std::f32::consts::PI;
        let mut yaw =
            self.gestures.yaw / (std::f32::consts::PI * 0.75) * camera_heading * (1.0 - sleep)
                + think * (0.35 + (t * 1.6).sin() * 0.12);
        // Read across a line, then glance at a different part of the screen.
        yaw += work * ((t * 0.85).sin() * 0.13 + (t * 0.31).sin() * 0.045) + drink * 0.50;
        yaw += self.game * (t * 1.7).sin() * 0.12;
        yaw += (camera_heading - yaw) * wave;
        yaw += (-0.35 - yaw) * watch;
        let mut pitch = self.gestures.pitch * (1.0 - sleep)
            + sleep * 1.42
            + work * (0.07 + (t * 0.63).sin() * 0.055 + (t * 6.0).sin() * 0.012)
            - think * 0.12
            - stretch * 0.26;
        pitch += (-0.25 - pitch) * wave;
        pitch += (0.48 - pitch) * watch;
        let mut head = [
            0.35,
            1.90 - sleep * 0.11 + (sleep * std::f32::consts::PI).sin() * 0.12 - think * 0.05
                + breath
                - stretch * 0.04,
            -0.48 + sleep * 0.70 - stretch * 0.08,
        ];
        let alarm = reaction.as_ref().map_or(0.0, |r| r.alarm * pose);
        if let Some(r) = &reaction {
            for c in 0..3 {
                head[c] += r.head[c] * pose;
            }
            yaw += (r.head_yaw - yaw) * pose;
            pitch += (r.head_pitch - pitch) * pose;
        }
        s.cube(head, [0.89, 0.70, 0.66], yaw, pitch, [217, 233, 234]);
        for side in [-1.0, 1.0] {
            s.speaker_ear(side, head, yaw, pitch);
        }
        let face = transform([0.0, -0.015, 0.339], head, yaw, pitch);
        s.cube(face, [0.72, 0.43, 0.026], yaw, pitch, [20, 40, 57]);
        for x in [-0.18, 0.18] {
            let eye = transform([x, 0.015, 0.358], head, yaw, pitch);
            let eye_h = 0.025
                + (0.125 + alarm * 0.10)
                    * (1.0 - sleep)
                    * (1.0 - stretch * 0.70)
                    * (1.0 - self.gestures.closure * (1.0 - alarm));
            s.cube(
                eye,
                [0.115 + alarm * 0.025, eye_h, 0.015],
                yaw,
                pitch,
                [58, 193, 255],
            );
        }
        // Upper arms stay attached; forearms and hands alternate above the keys.
        let mut hands = [[0.0; 3]; 2];
        for (i, x) in [-0.09, 0.79].iter().enumerate() {
            let tap = ((t * (18.0 + self.game * 9.0) + i as f32 * std::f32::consts::PI).sin()
                * (0.04 + self.game * 0.018))
                * work;
            let target_x = 0.12 + i as f32 * 0.46;
            let typing_raise = (work / 0.55).clamp(0.0, 1.0);
            let typing_reach = ((work - 0.55) / 0.45).clamp(0.0, 1.0);
            let mut hand = [
                *x + (target_x - *x) * work,
                0.99 + typing_raise * 0.46 + tap,
                -0.24 + typing_reach * 0.39,
            ];
            // First raise the palms behind the edge, then lay them on the desk.
            let raise = if i == 0 {
                (sleep / 0.45).clamp(0.0, 1.0)
            } else {
                0.0
            };
            let reach = if i == 0 {
                ((sleep - 0.45) / 0.55).clamp(0.0, 1.0)
            } else {
                0.0
            };
            hand[0] += ((if i == 0 { -0.26 } else { 0.96 }) - hand[0]) * raise;
            hand[1] += (1.50 - hand[1]) * raise;
            hand[2] += (0.12 - hand[2]) * reach;
            if i == 1 {
                // The right arm hangs beside the chair, well behind the table edge.
                let hanging = [0.94, 0.78, -0.48];
                for c in 0..3 {
                    hand[c] += (hanging[c] - hand[c]) * sleep;
                }
            }
            if i == 1 {
                // Right palm follows the temple as the head tilts and scratches.
                let scratch = transform(
                    [0.65, 0.20 + (t * 10.0).sin() * 0.065, -0.07],
                    head,
                    yaw,
                    pitch,
                );
                for c in 0..3 {
                    hand[c] += (scratch[c] - hand[c]) * think;
                }
                let target = drink_hand(self.work_gestures.age);
                for c in 0..3 {
                    hand[c] += (target[c] - hand[c]) * drink;
                }
            }
            let shoulder = transform(
                [
                    *x - 0.35,
                    0.26 + if i == 1 { think * 0.04 } else { 0.0 } + alarm * 0.10,
                    0.01,
                ],
                body,
                body_yaw,
                body_pitch,
            );
            let side = if i == 0 { -1.0 } else { 1.0 };
            let reaching = transform([side * 0.72, 0.94, -0.04], body, body_yaw, body_pitch);
            for c in 0..3 {
                hand[c] += (reaching[c] - hand[c]) * stretch;
            }
            let target = if i == 1 {
                [
                    1.02 + (self.gestures.extra_age * 9.0).sin() * 0.10,
                    1.95,
                    -0.62,
                ]
            } else {
                [0.10, 1.59, -0.05]
            };
            let amount = if i == 1 { wave } else { watch };
            for c in 0..3 {
                hand[c] += (target[c] - hand[c]) * amount;
            }
            if let Some(target) = event.and_then(|e| e.hand(i)) {
                for c in 0..3 {
                    hand[c] += (target[c] - hand[c]) * pose;
                }
            }
            let (elbow, hand) = arm_pose_lift(
                shoulder,
                hand,
                if i == 0 { -1.0 } else { 1.0 },
                raise.max(drink).max(stretch).max(amount),
            );
            hands[i] = hand;
            s.limb(shoulder, elbow, [154, 184, 199]);
            s.limb(elbow, hand, [217, 233, 234]);
            s.block(hand, [0.23, 0.13, 0.22], [188, 214, 223]);
            if i == 0 && watch > 0.05 {
                // A small illuminated wrist display makes the gesture legible.
                s.block(
                    [hand[0], hand[1] + 0.072, hand[2]],
                    [0.16, 0.018, 0.15],
                    [20, 52, 74],
                );
                for x in [-0.04, 0.04] {
                    s.block(
                        [hand[0] + x, hand[1] + 0.085, hand[2]],
                        [0.028, 0.009, 0.07],
                        [75, 210, 250],
                    );
                }
            }
        }
        let age = self.work_gestures.age;
        let held = if self.work_gestures.kind == 2 && (1.4..5.8).contains(&age) {
            drink
        } else {
            0.0
        };
        let mug_home = [1.03, 1.46, 0.15];
        let mug = std::array::from_fn(|i| {
            mug_home[i] + (hands[1][i] + (if i == 0 { 0.13 } else { 0.0 }) - mug_home[i]) * held
        });
        let sip = ((age - 2.6) / 0.6).clamp(0.0, 1.0) * ((4.3 - age) / 0.6).clamp(0.0, 1.0) * held;
        let tilt = -0.50 * sip;
        s.cube(mug, [0.24, 0.30, 0.25], 0.0, tilt, [65, 185, 195]);
        s.cube(
            transform([0.0, 0.155, 0.0], mug, 0.0, tilt),
            [0.18, 0.014, 0.18],
            0.0,
            tilt,
            [69, 49, 38],
        );
        s.cube(
            transform([-0.13, 0.0, 0.0], mug, 0.0, tilt),
            [0.13, 0.15, 0.07],
            0.0,
            tilt,
            [65, 185, 195],
        );
        // Tiny real bitmap terminal glyphs. Scroll faster while working.
        let tick = (t * if working { 5.0 } else { 0.6 }) as usize;
        let commands = ["> run", "load...", "[ok]", "> build", "010101", "ready"];
        for row in 0..if gaming { 0 } else { 5 } {
            let line = commands[(tick / 3 + row) % commands.len()];
            for (col, ch) in line.bytes().enumerate() {
                let glyph = glyph(ch);
                for (gy, bits) in glyph.iter().enumerate() {
                    for gx in 0..3 {
                        if bits & (1 << (2 - gx)) != 0 {
                            let x = -0.07 + (col * 4 + gx) as f32 * 0.023;
                            let y = 1.96 - (row * 7 + gy) as f32 * 0.014;
                            s.quad(
                                [
                                    [x, y, 0.808],
                                    [x + 0.018, y, 0.808],
                                    [x + 0.018, y + 0.011, 0.808],
                                    [x, y + 0.011, 0.808],
                                ],
                                if sleep > 0.8 {
                                    [36, 78, 82]
                                } else {
                                    [99, 227, 192]
                                },
                            );
                        }
                    }
                }
            }
        }
        if gaming {
            draw_arcade(s, t);
        }
        if !gaming && t.fract() < 0.55 && sleep < 0.5 {
            s.block([0.08, 1.44, 0.806], [0.07, 0.025, 0.006], [125, 231, 223]);
        }
        events::draw(s, event, hands, head);
        if sleep > 0.8 {
            let lift = (t * 0.35).fract();
            // Screen-facing glyph high above the head, readable from every orbit.
            let anchor = s.project([head[0], 3.0 + lift * 0.65, head[2]]);
            let unit = (s.scale / 16.0).round().max(2.0) as usize;
            for (row, bits) in [7, 1, 2, 4, 7].iter().enumerate() {
                for col in 0..3 {
                    if bits & (1 << col) != 0 {
                        for dy in 0..unit {
                            for dx in 0..unit {
                                let x = anchor[0] as isize + ((2 - col) * unit + dx) as isize;
                                let y = anchor[1] as isize + (row * unit + dy) as isize;
                                if x >= 0 && y >= 0 && x < s.width as isize && y < s.height as isize
                                {
                                    s.pixels[y as usize * s.width + x as usize] = [145, 215, 250];
                                }
                            }
                        }
                    }
                }
            }
        }
        let width = s.width / 2;
        let height = s.height / 2;
        let mut packed = vec![0; width * height * 2];
        for y in 0..height {
            for x in 0..width {
                let mut rgb = [0_u16; 3];
                for dy in 0..2 {
                    for dx in 0..2 {
                        let p = s.pixels[(y * 2 + dy) * s.width + x * 2 + dx];
                        for c in 0..3 {
                            rgb[c] += p[c] as u16;
                        }
                    }
                }
                let i = y * width + x;
                for c in 0..3 {
                    rgb[c] /= 4;
                    if let Some(o) = overlay {
                        let a = o[i * 4 + 3] as u16;
                        rgb[c] = (rgb[c] * (255 - a) + o[i * 4 + c] as u16 * a + 127) / 255;
                    }
                }
                let value = ((rgb[0] & 0xf8) << 8) | ((rgb[1] & 0xfc) << 3) | (rgb[2] >> 3);
                packed[i * 2..i * 2 + 2].copy_from_slice(&value.to_be_bytes());
            }
        }
        packed
    }
}

fn draw_arcade(s: &mut Surface, t: f32) {
    // Tiny arcade shooter drawn on the laptop plane, not a floating UI overlay.
    let mut rect = |x: f32, y: f32, w: f32, h: f32, color: Color| {
        s.quad(
            [
                [x, y, 0.804],
                [x + w, y, 0.804],
                [x + w, y + h, 0.804],
                [x, y + h, 0.804],
            ],
            color,
        );
    };
    let ship = 0.35 + (t * 1.7).sin() * 0.32;
    rect(ship - 0.065, 1.46, 0.13, 0.035, [85, 215, 255]);
    rect(ship - 0.022, 1.495, 0.044, 0.04, [190, 245, 255]);
    rect(
        ship - 0.012,
        1.55 + (t * 1.8).fract() * 0.39,
        0.024,
        0.065,
        [255, 231, 103],
    );
    for row in 0..2 {
        for col in 0..3 {
            if (t * 1.8) as usize % 6 == row * 3 + col && (t * 1.8).fract() > 0.65 {
                continue;
            }
            let x = 0.07 + col as f32 * 0.25 + (t * 1.1).sin() * 0.04;
            let y = 1.76 + row as f32 * 0.13;
            let color = if row == 0 {
                [255, 136, 94]
            } else {
                [181, 153, 255]
            };
            rect(x, y, 0.13, 0.065, color);
            rect(x - 0.02, y - 0.025, 0.035, 0.025, color);
            rect(x + 0.115, y - 0.025, 0.035, 0.025, color);
        }
    }
    for i in 0..5 {
        rect(
            -0.04 + i as f32 * 0.055,
            1.99,
            0.035,
            0.014,
            if i <= (t as usize / 2) % 5 {
                [108, 235, 163]
            } else {
                [42, 70, 83]
            },
        );
    }
}

fn glyph(c: u8) -> [u8; 5] {
    match c {
        b'>' => [4, 2, 1, 2, 4],
        b'r' => [0, 6, 5, 4, 4],
        b'u' => [0, 5, 5, 5, 7],
        b'n' => [0, 6, 5, 5, 5],
        b'l' => [4, 4, 4, 4, 6],
        b'o' => [0, 2, 5, 5, 2],
        b'a' => [0, 3, 5, 5, 3],
        b'd' => [1, 1, 3, 5, 3],
        b'b' => [4, 4, 6, 5, 6],
        b'i' => [2, 0, 2, 2, 2],
        b'k' => [4, 5, 6, 6, 5],
        b'e' => [0, 2, 7, 4, 3],
        b'y' => [0, 5, 3, 1, 6],
        b'[' => [3, 2, 2, 2, 3],
        b']' => [6, 2, 2, 2, 6],
        b'0' => [7, 5, 5, 5, 7],
        b'1' => [2, 6, 2, 2, 7],
        b'.' => [0, 0, 0, 0, 2],
        _ => [0; 5],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn idle_game_uses_keyboard_and_exits_for_actual_work() {
        let mut r = RobotRenderer::new(240, 280, Some(2)).unwrap();
        for i in 0..90 {
            r.render(i as f32 / 30.0, false, 60.0, 45.0, None, false);
        }
        assert!(r.pastime.gaming && r.sleep < 0.01 && r.work > 0.99);
        for i in 90..150 {
            r.render(i as f32 / 30.0, true, 0.0, 45.0, None, false);
        }
        assert!(!r.pastime.gaming && r.game < 0.01 && r.work > 0.99);
    }
    #[test]
    fn sleep_clears_desk_and_drink_reach_keeps_arm_lengths() {
        for i in 0..=100 {
            let sleep = i as f32 / 100.0;
            let body = [0.35, 1.10 + sleep * 0.10, -0.51 - sleep * 0.04];
            // Even the frontmost torso vertex stays behind the near desk edge.
            for y in [-0.33, 0.33] {
                for z in [-0.215, 0.215] {
                    assert!(transform([0.0, y, z], body, 0.0, sleep * 0.62)[2] < -0.09);
                }
            }
            let head_y = 1.90 - sleep * 0.11 + (sleep * std::f32::consts::PI).sin() * 0.12;
            let (sin, cos) = (sleep * 1.42).sin_cos();
            assert!(head_y - cos * 0.35 - sin * 0.33 - 0.012 > 1.39);
            for side in [-1.0, 1.0] {
                let raise = if side < 0.0 {
                    (sleep / 0.45).clamp(0.0, 1.0)
                } else {
                    0.0
                };
                let reach = ((sleep - 0.45) / 0.55).clamp(0.0, 1.0);
                let target = if side > 0.0 {
                    [
                        0.79 + sleep * 0.15,
                        0.99 - sleep * 0.21,
                        -0.24 - sleep * 0.24,
                    ]
                } else {
                    [
                        0.35 + side * (0.44 + 0.17 * raise),
                        0.99 + 0.51 * raise,
                        -0.24 + 0.36 * reach,
                    ]
                };
                let shoulder = transform([side * 0.44, 0.26, 0.01], body, 0.0, sleep * 0.62);
                let (elbow, hand) = arm_pose_lift(shoulder, target, side, raise);
                for (a, b) in [(shoulder, elbow), (elbow, hand)] {
                    for step in 0..=20 {
                        let p: V =
                            std::array::from_fn(|c| a[c] + (b[c] - a[c]) * step as f32 / 20.0);
                        assert!(
                            !(p[2] > -0.18 && p[1] > 1.015 && p[1] < 1.395),
                            "arm crosses tabletop at sleep {sleep}: {p:?}"
                        );
                    }
                }
            }
            let shoulder = [0.79, 1.36, -0.50];
            let target = drink_hand(i as f32 * 0.07);
            let reach = (0..3)
                .map(|c| (target[c] - shoulder[c]).powi(2))
                .sum::<f32>()
                .sqrt();
            assert!(reach < 0.739, "unreachable drink grip at {i}: {reach}");
        }
    }
    #[test]
    fn orbit_eases_quarter_turns_and_wraps_with_matching_cache() {
        let mut r = RobotRenderer::new(240, 280, Some(7)).unwrap();
        r.render(0.0, false, 0.0, 45.0, None, false);
        for turn in 0..4 {
            r.rotate_view();
            r.rotate_view(); // held/repeated input must not queue a second turn
            for i in 1..=30 {
                r.render(turn as f32 + i as f32 / 30.0, false, 0.0, 45.0, None, false);
                if i == 12 {
                    assert!(
                        r.orbit > r.orbit_start
                            && r.orbit < r.orbit_start + std::f32::consts::FRAC_PI_2
                    );
                }
            }
            let expected =
                ((turn + 1) as f32 * std::f32::consts::FRAC_PI_2).rem_euclid(std::f32::consts::TAU);
            assert!((r.orbit - expected).abs() < 0.0001);
            assert_eq!(r.background.orbit, r.frame.orbit);
        }
    }
    #[test]
    fn animation_and_wakeup() {
        let mut r = RobotRenderer::new(240, 280, Some(7)).unwrap();
        let idle = r.render(0.0, false, 0.0, 45.0, None, false);
        assert_eq!(idle.len(), 240 * 280 * 2);
        assert!(idle.iter().any(|&v| v != 0));
        for i in 1..=60 {
            r.render(i as f32 / 30.0, false, 60.0, 45.0, None, false);
        }
        assert!(r.sleep > 0.99);
        let sleep = r.render(2.1, false, 60.0, 45.0, None, false);
        assert_ne!(idle, sleep);
        for i in 64..=130 {
            r.render(i as f32 / 30.0, true, 0.0, 45.0, None, false);
        }
        assert!(r.sleep < 0.01);
        assert!(r.work > 0.99);
    }
    #[test]
    fn dimensions_are_bounded() {
        assert!(RobotRenderer::new(0, 280, Some(7)).is_err());
        assert!(RobotRenderer::new(240, 10000, Some(7)).is_err());
    }

    #[test]
    fn arms_do_not_stretch_and_thinking_wakes() {
        let shoulder = [0.79, 1.36, -0.50];
        let distance = |a: V, b: V| (0..3).map(|i| (a[i] - b[i]).powi(2)).sum::<f32>().sqrt();
        for target in [[0.58, 1.45, 0.15], [0.86, 1.96, -0.48], [0.79, 1.0, 2.0]] {
            let (elbow, hand) = arm_pose(shoulder, target, 1.0);
            assert!((distance(shoulder, elbow) - 0.36).abs() < 0.001);
            assert!((distance(elbow, hand) - 0.38).abs() < 0.001);
        }
        let mut r = RobotRenderer::new(240, 280, Some(7)).unwrap();
        r.sleep = 1.0;
        for i in 0..100 {
            r.render(i as f32 / 30.0, true, 60.0, 45.0, None, true);
        }
        assert!(r.sleep < 0.01 && r.work < 0.01 && r.think > 0.99);
        for i in 100..190 {
            r.render(i as f32 / 30.0, true, 0.0, 45.0, None, false);
        }
        assert!(r.think < 0.01 && r.work > 0.99);
    }
}

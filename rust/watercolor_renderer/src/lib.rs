use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::time::{SystemTime, UNIX_EPOCH};

const MAIN: [f32; 3] = [0.961, 0.980, 0.965];
const LOW: [f32; 3] = [0.0, 0.314, 0.988];
const MID: [f32; 3] = [0.686, 0.871, 0.980];
const HIGH: [f32; 3] = [0.980, 0.988, 0.941];
const TRANSITION: [f32; 3] = [0.200, 0.627, 0.992];

#[inline]
fn clamp(value: f32, low: f32, high: f32) -> f32 {
    value.max(low).min(high)
}

#[inline]
fn smoothstep(edge0: f32, edge1: f32, value: f32) -> f32 {
    let amount = clamp((value - edge0) / (edge1 - edge0).max(1.0e-6), 0.0, 1.0);
    amount * amount * (3.0 - 2.0 * amount)
}

#[inline]
fn mix(a: f32, b: f32, amount: f32) -> f32 {
    a + (b - a) * amount
}

#[inline]
fn cubic(p0: f32, p1: f32, p2: f32, p3: f32, amount: f32) -> f32 {
    // Catmull-Rom reconstruction is close to Pillow's bicubic resize and,
    // unlike bilinear interpolation, retains narrow pigment boundaries when
    // the fluid field is rendered below the LCD's native resolution.
    let a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3;
    let b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3;
    let c = -0.5 * p0 + 0.5 * p2;
    ((a * amount + b) * amount + c) * amount + p1
}

#[inline]
fn sample_bicubic(
    pixels: &[[f32; 3]],
    width: usize,
    height: usize,
    x: f32,
    y: f32,
) -> [f32; 3] {
    let base_x = x.floor() as isize;
    let base_y = y.floor() as isize;
    let fx = x - base_x as f32;
    let fy = y - base_y as f32;
    let mut result = [0.0_f32; 3];
    for (channel, value) in result.iter_mut().enumerate() {
        let mut rows = [0.0_f32; 4];
        for (row_index, offset_y) in (-1_isize..=2).enumerate() {
            let sy = (base_y + offset_y).clamp(0, height as isize - 1) as usize;
            let mut samples = [0.0_f32; 4];
            for (column_index, offset_x) in (-1_isize..=2).enumerate() {
                let sx = (base_x + offset_x).clamp(0, width as isize - 1) as usize;
                samples[column_index] = pixels[sy * width + sx][channel];
            }
            rows[row_index] = cubic(samples[0], samples[1], samples[2], samples[3], fx);
        }
        *value = cubic(rows[0], rows[1], rows[2], rows[3], fy);
    }
    result
}

#[inline]
fn sample_bilinear(
    pixels: &[[f32; 3]],
    width: usize,
    height: usize,
    x: f32,
    y: f32,
) -> [f32; 3] {
    let x0 = x.floor().clamp(0.0, width as f32 - 1.0) as usize;
    let y0 = y.floor().clamp(0.0, height as f32 - 1.0) as usize;
    let x1 = (x0 + 1).min(width - 1);
    let y1 = (y0 + 1).min(height - 1);
    let fx = x - x0 as f32;
    let fy = y - y0 as f32;
    let mut result = [0.0_f32; 3];
    for (channel, value) in result.iter_mut().enumerate() {
        let top = mix(
            pixels[y0 * width + x0][channel],
            pixels[y0 * width + x1][channel],
            fx,
        );
        let bottom = mix(
            pixels[y1 * width + x0][channel],
            pixels[y1 * width + x1][channel],
            fx,
        );
        *value = mix(top, bottom, fy);
    }
    result
}

#[inline]
fn luminance(pixel: [f32; 3]) -> f32 {
    pixel[0] * 0.2126 + pixel[1] * 0.7152 + pixel[2] * 0.0722
}

#[inline]
fn hash(x: f32, y: f32) -> f32 {
    ((x * 12.9898 + y * 4.1414).sin() * 43758.5453).rem_euclid(1.0)
}

#[inline]
fn noise(x: f32, y: f32) -> f32 {
    let ix = x.floor();
    let iy = y.floor();
    let mut ux = x - ix;
    let mut uy = y - iy;
    ux = ux * ux * (3.0 - 2.0 * ux);
    uy = uy * uy * (3.0 - 2.0 * uy);
    let a = hash(ix, iy);
    let b = hash(ix + 1.0, iy);
    let c = hash(ix, iy + 1.0);
    let d = hash(ix + 1.0, iy + 1.0);
    let value = (a * (1.0 - ux) + b * ux) * (1.0 - uy) + (c * (1.0 - ux) + d * ux) * uy;
    value * value
}

fn fbm(mut x: f32, mut y: f32) -> f32 {
    let mut value = 0.0;
    let mut amplitude = 0.5;
    let cosine = 0.5_f32.cos();
    let sine = 0.5_f32.sin();
    for _ in 0..4 {
        value += amplitude * noise(x, y);
        let rotated_x = cosine * x + sine * y;
        let rotated_y = -sine * x + cosine * y;
        x = rotated_x * 2.0 + 100.0;
        y = rotated_y * 2.0 + 100.0;
        amplitude *= 0.5;
    }
    value
}

#[inline]
fn sample_fbm(table: &[f32], x: f32, y: f32, smooth: bool) -> f32 {
    const SIZE: usize = 128;
    const SCALE: f32 = SIZE as f32 / 16.0;
    let px = (x * SCALE).rem_euclid(SIZE as f32);
    let py = (y * SCALE).rem_euclid(SIZE as f32);
    let x0 = px.floor() as usize % SIZE;
    let y0 = py.floor() as usize % SIZE;
    if !smooth {
        return table[y0 * SIZE + x0];
    }
    let x1 = (x0 + 1) % SIZE;
    let y1 = (y0 + 1) % SIZE;
    let mut fx = px - px.floor();
    let mut fy = py - py.floor();
    fx = fx * fx * (3.0 - 2.0 * fx);
    fy = fy * fy * (3.0 - 2.0 * fy);
    let top = mix(table[y0 * SIZE + x0], table[y0 * SIZE + x1], fx);
    let bottom = mix(table[y1 * SIZE + x0], table[y1 * SIZE + x1], fx);
    mix(top, bottom, fy)
}

#[derive(Clone, Copy)]
struct RenderInput {
    phase: f32,
    level: f32,
    bands: [f32; 4],
    cumulative: [f32; 4],
}

#[pyclass]
struct OrbRenderer {
    width: usize,
    height: usize,
    diameter: f32,
    render_width: usize,
    render_height: usize,
    x: Vec<f32>,
    y: Vec<f32>,
    full_distance: Vec<f32>,
    audio_reactivity: f32,
    idle_speed: f32,
    speech_motion: f32,
    phase_offset: f32,
    smooth_fbm: bool,
    fbm_table: Vec<f32>,
    pool: ThreadPool,
}

#[pymethods]
impl OrbRenderer {
    #[new]
    #[pyo3(signature = (
        width=240,
        height=280,
        diameter=168,
        render_scale=0.37,
        smooth_fbm=false,
        temporal_3d=false,
        audio_reactivity=3.2,
        idle_speed=0.70,
        speech_motion=3.6,
        threads=2
    ))]
    fn new(
        width: usize,
        height: usize,
        diameter: usize,
        render_scale: f32,
        smooth_fbm: bool,
        temporal_3d: bool,
        audio_reactivity: f32,
        idle_speed: f32,
        speech_motion: f32,
        threads: usize,
    ) -> PyResult<Self> {
        if width == 0 || height == 0 {
            return Err(PyValueError::new_err("display dimensions must be positive"));
        }
        // Reserved for the more expensive temporal-noise quality tier. The
        // current native backend shares the continuously advected 2D path.
        let _ = temporal_3d;
        let scale = clamp(render_scale, 0.2, 1.0);
        let render_width = ((width as f32) * scale).round().max(1.0) as usize;
        let render_height = ((height as f32) * scale).round().max(1.0) as usize;
        let render_radius = (diameter as f32 * scale * 0.5).max(1.0);
        let center_x = (render_width as f32 - 1.0) * 0.5;
        let center_y = (render_height as f32 - 1.0) * 0.5 - 10.0 * scale;
        let mut x = Vec::with_capacity(render_width * render_height);
        let mut y = Vec::with_capacity(render_width * render_height);
        for py in 0..render_height {
            for px in 0..render_width {
                x.push((px as f32 - center_x) / render_radius);
                y.push((py as f32 - center_y) / render_radius);
            }
        }
        let full_center_x = (width as f32 - 1.0) * 0.5;
        let full_center_y = (height as f32 - 1.0) * 0.5 - 10.0;
        let mut full_distance = Vec::with_capacity(width * height);
        for py in 0..height {
            for px in 0..width {
                let dx = px as f32 - full_center_x;
                let dy = py as f32 - full_center_y;
                full_distance.push((dx * dx + dy * dy).sqrt());
            }
        }
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.subsec_nanos() as f32)
            .unwrap_or(1.0);
        let phase_offset = hash(seed * 1.0e-6, 78.233) * 71.0;
        let mut fbm_table = vec![0.0_f32; 128 * 128];
        for py in 0..128 {
            for px in 0..128 {
                fbm_table[py * 128 + px] = fbm(px as f32 * 0.125, py as f32 * 0.125);
            }
        }
        let pool = ThreadPoolBuilder::new()
            .num_threads(threads.clamp(1, 4))
            .thread_name(|index| format!("watercolor-{index}"))
            .build()
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self {
            width,
            height,
            diameter: diameter as f32,
            render_width,
            render_height,
            x,
            y,
            full_distance,
            audio_reactivity: clamp(audio_reactivity, 0.5, 5.0),
            idle_speed: clamp(idle_speed, 0.05, 1.0),
            speech_motion: clamp(speech_motion, 0.5, 5.0),
            phase_offset,
            smooth_fbm,
            fbm_table,
            pool,
        })
    }

    #[pyo3(signature = (
        phase,
        level,
        peak=0.0,
        bands=vec![0.0, 0.0, 0.0, 0.0],
        cumulative=vec![0.0, 0.0, 0.0, 0.0],
        visual_scale=1.0,
        overlay=None
    ))]
    fn rgb565<'py>(
        &self,
        py: Python<'py>,
        phase: f32,
        level: f32,
        peak: f32,
        bands: Vec<f32>,
        cumulative: Vec<f32>,
        visual_scale: f32,
        overlay: Option<&[u8]>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let mut band_values = [0.0_f32; 4];
        let mut cumulative_values = [0.0_f32; 4];
        for index in 0..4 {
            band_values[index] = clamp(*bands.get(index).unwrap_or(&0.0), 0.0, 1.0);
            cumulative_values[index] = *cumulative.get(index).unwrap_or(&0.0);
        }
        let expected_overlay = self.width * self.height * 4;
        if let Some(pixels) = overlay {
            if pixels.len() != expected_overlay {
                return Err(PyValueError::new_err(format!(
                    "overlay must contain {expected_overlay} RGBA bytes"
                )));
            }
        }
        let input = RenderInput {
            phase,
            level: clamp(level, 0.0, 1.0),
            bands: band_values,
            cumulative: cumulative_values,
        };
        let frame = py.allow_threads(|| self.render(input, peak, visual_scale, overlay));
        Ok(PyBytes::new_bound(py, &frame))
    }

    #[getter]
    fn backend(&self) -> &'static str {
        "rust"
    }
}

impl OrbRenderer {
    fn render_fluid(&self, input: RenderInput) -> Vec<[f32; 3]> {
        let mut output = vec![[0.0_f32; 3]; self.render_width * self.render_height];
        let reactivity = self.audio_reactivity;
        let motion = self.speech_motion;
        let travelled = [
            input.cumulative[0] * reactivity * motion,
            input.cumulative[1] * reactivity * motion,
            input.cumulative[2] * reactivity * motion,
            input.cumulative[3] * reactivity * motion,
        ];
        let reactive = [
            clamp(input.bands[0] * reactivity * 1.35, 0.0, 1.55),
            clamp(input.bands[1] * reactivity * 1.35, 0.0, 1.55),
            clamp(input.bands[2] * reactivity * 1.35, 0.0, 1.55),
            clamp(input.bands[3] * reactivity * 1.35, 0.0, 1.55),
        ];
        let time = (input.phase + self.phase_offset) * self.idle_speed
            + travelled[3] * 0.10;
        let voice = clamp(
            input.level * 1.15 + reactive.iter().sum::<f32>() * 0.25 * 0.72,
            0.0,
            1.6,
        );
        let vertical = 0.01 * (std::f32::consts::FRAC_PI_2 * time).sin();
        let smooth_fbm = self.smooth_fbm;
        let table = &self.fbm_table;
        self.pool.install(|| {
            output
                .par_iter_mut()
                .enumerate()
                .for_each(|(index, pixel)| {
                    let mut u = self.x[index] * 0.5 + 0.5;
                    let mut v = 1.0 - ((self.y[index] - vertical) * 0.5 + 0.5);
                    let noise_x = noise(u + time * 0.09, v + 74.8572 + time * 0.04);
                    let noise_y = noise(u + 203.91282 - time * 0.05, v + 10.0 + time * 0.07);
                    u += (noise_x - 0.5) * 0.38;
                    v += (noise_y - 0.5) * 0.19;

                    let watercolor_a = noise(u * 18.0 + 344.91282 + time * 0.3, v * 18.0);
                    let watercolor_b = noise(u * 39.6 + 723.937, v * 39.6 + time * 0.4);
                    let watercolor = watercolor_a + watercolor_b * 0.5;
                    let watercolor_warp = 0.01 + voice * 0.004;
                    u += watercolor * watercolor_warp;
                    v += watercolor * watercolor_warp - 0.09;

                    let texture_a = noise(
                        (u + time * 0.011 + travelled[0] * 0.012) * 9.0 + 63.861,
                        (v + time * 0.007 + travelled[1] * 0.009) * 9.0 + 368.937,
                    );
                    let texture_b = noise(
                        (u - time * 0.009 - travelled[2] * 0.010) * 9.0 + 272.861,
                        (1.0 - v + time * 0.006 + travelled[3] * 0.011) * 9.0 + 829.937,
                    );
                    let displacement0 = (texture_a * 0.64 + texture_b * 0.36 - 0.5) * 0.08;
                    let displacement1 = (texture_b * 0.56 + watercolor_a * 0.44 - 0.5) * 0.08;
                    let displacement3 = (watercolor_b * 0.58 + texture_a * 0.42 - 0.5) * 0.08;
                    u += displacement0;
                    v += displacement0;

                    let st_x = u * 1.25;
                    let st_y = v * 1.25;
                    let qx_clock = 0.075 * (time + travelled[3] * 0.175);
                    let qy_clock = 0.075 * (time + travelled[0] * 0.136);
                    let q_x = sample_fbm(table, st_x * 0.5 + qx_clock, st_y * 0.5 + qx_clock, smooth_fbm);
                    let q_y = sample_fbm(table, st_x * 0.5 + qy_clock, st_y * 0.5 + qy_clock, smooth_fbm);
                    let rx_clock = 0.15 * (time + travelled[1] * 0.234);
                    let ry_clock = 0.126 * (time + travelled[2] * 0.165);
                    let r_x = sample_fbm(table, st_x + q_x + 0.3 + rx_clock, st_y + q_y + 9.2 + rx_clock, smooth_fbm);
                    let r_y = sample_fbm(table, st_x + q_x + 8.3 + ry_clock, st_y + q_y + 0.8 + ry_clock, smooth_fbm);
                    let field = sample_fbm(table, st_x + r_x - q_x, st_y + r_y - q_y, smooth_fbm);
                    let full_fbm = clamp((field + 0.6 * field * field + 0.7 * field + 0.5) * 0.5, 0.0, f32::MAX).powf(0.55);
                    let fbm_centered = full_fbm - 0.5;
                    let flow_x = noise_x - 0.5;
                    let flow_y = noise_y - 0.5;
                    let signed_flow = noise_x - noise_y;
                    let signed_watercolor = watercolor_a - watercolor_b;
                    let signed_texture = texture_a - texture_b;
                    let signed_q = q_x - q_y;
                    let signed_r = r_x - r_y;
                    let speech_warp = 1.0 + voice * 1.05;
                    let local_drive = 0.20 + voice * 0.20;

                    let layer1_x = u + (signed_q * 0.24 + signed_texture * 0.10 - signed_flow * 0.07) * local_drive;
                    let layer1_y = v + (signed_r * 0.22 - signed_watercolor * 0.08 + signed_flow * 0.06) * local_drive;
                    let layer2_x = u + (-signed_r * 0.22 + signed_watercolor * 0.11 + signed_q * 0.06) * local_drive;
                    let layer2_y_base = v + (signed_q * 0.20 + signed_texture * 0.10 - signed_flow * 0.05) * local_drive;
                    let layer3_x = u + (signed_texture * 0.20 - signed_q * 0.13 + signed_r * 0.06) * local_drive;
                    let layer3_y_base = v + (-signed_watercolor * 0.18 - signed_r * 0.12 + signed_flow * 0.09) * local_drive;
                    let base_y = layer1_y + fbm_centered * 1.05 + displacement0 + flow_y * 0.20 * speech_warp + 0.025;
                    let layer1_noise = noise(
                        (layer1_x + fbm_centered * 1.20 + flow_x * 0.22 * speech_warp) * 2.0 + travelled[0] * 0.015,
                        base_y * 2.0 + time * 0.5 + travelled[0] * 0.15,
                    ) * 2.0;
                    let threshold_reactivity = 1.0 + voice * 0.65;
                    let threshold1 = threshold_reactivity * (signed_q * 0.70 + signed_r * 0.42 + signed_watercolor * 0.17 + signed_flow * 0.22);
                    let mut layer1 = smoothstep(
                        layer1_noise - 1.8,
                        layer1_noise + 1.8,
                        (base_y - 0.5 + threshold1) * (5.0 - reactive[0] * 0.45) + 0.5,
                    ).powf(0.8);

                    let layer2_y = layer2_y_base + fbm_centered * 0.78 + displacement1 - flow_x * 0.18 * speech_warp + 0.025;
                    let layer2_noise = noise(
                        (layer2_x - fbm_centered * 0.72 + flow_y * 0.20 * speech_warp) * 4.0 - travelled[1] * 0.11 + 293.0,
                        layer2_y * 4.0 + time - travelled[1] * 0.25,
                    ) * 2.0;
                    let threshold2 = threshold_reactivity * (-signed_r * 0.62 + signed_q * 0.33 + signed_texture * 0.20 - signed_flow * 0.18);
                    let mut layer2 = smoothstep(
                        layer2_noise - (0.9 + reactive[1] * 0.70) * 1.5,
                        layer2_noise + (0.9 + reactive[1] * 1.30) * 1.5,
                        (layer2_y - 0.6 + threshold2) * (5.0 - reactive[1] * 1.45) + 0.5,
                    ).powf(0.9);

                    let layer3_y = layer3_y_base + fbm_centered * 0.92 + displacement3 + (flow_x - flow_y) * 0.15 * speech_warp;
                    let layer3_noise = noise(
                        (layer3_x + fbm_centered * 1.05 - flow_y * 0.18 * speech_warp) * 6.0 + travelled[2] * 0.18 + 153.0,
                        layer3_y * 6.0 + time * 1.2 + travelled[2] * 1.2,
                    ) * 2.0;
                    let threshold3 = threshold_reactivity * (-signed_q * 0.38 - signed_r * 0.51 + signed_watercolor * 0.14 + signed_texture * 0.16 + signed_flow * 0.20);
                    let mut layer3 = smoothstep(
                        layer3_noise - (1.05 + reactive[2] * 0.25),
                        layer3_noise + (1.05 + reactive[2] * 0.40),
                        (layer3_y - 0.9 + threshold3) * (6.0 - reactive[2] * 1.2) + 0.5,
                    );

                    if smooth_fbm {
                        let edge1 = layer1 * (1.0 - layer1) * 4.0;
                        let edge2 = layer2 * (1.0 - layer2) * 4.0;
                        let edge3 = layer3 * (1.0 - layer3) * 4.0;
                        layer1 = clamp(layer1 + signed_watercolor * 0.18 * edge1, 0.0, 1.0);
                        layer2 = clamp(layer2 + signed_texture * 0.15 * edge2, 0.0, 1.0);
                        layer3 = clamp(layer3 + (watercolor_b - texture_a) * 0.12 * edge3, 0.0, 1.0);
                        layer1 = smoothstep(0.05, 0.95, layer1);
                        layer2 = smoothstep(0.05, 0.95, layer2);
                        layer3 = smoothstep(0.05, 0.95, layer3);
                    }

                    let pigment1 = clamp(layer1 * 0.41 + 0.464 + signed_q * 0.09 + signed_r * 0.05, 0.28, 0.96);
                    let inverse = 1.0 - pigment1;
                    for channel in 0..3 {
                        let mut value = LOW[channel] * inverse * inverse
                            + TRANSITION[channel] * 2.0 * inverse * pigment1
                            + MAIN[channel] * pigment1 * pigment1;
                        let middle = mix(MID[channel], MAIN[channel], layer2);
                        let middle_amount = (1.0 - layer1) * 0.150 + layer1 * 0.34;
                        value = mix(value, middle, middle_amount);
                        let high = mix(HIGH[channel], MAIN[channel], layer3);
                        let high_amount = layer1 * layer2 * 0.56 + (1.0 - layer1) * layer3 * 0.055;
                        value = mix(value, high, high_amount);
                        if !smooth_fbm {
                            value += [0.0, 0.026, 0.004][channel];
                        }
                        *pixel.get_mut(channel).unwrap() = clamp(value, 0.0, 1.0) * 255.0;
                    }
                });
        });
        output
    }

    fn render(
        &self,
        input: RenderInput,
        _peak: f32,
        visual_scale: f32,
        overlay: Option<&[u8]>,
    ) -> Vec<u8> {
        let fluid = self.render_fluid(input);
        let mut packed = vec![0_u8; self.width * self.height * 2];
        let mask_radius = self.diameter * 0.5 * clamp(visual_scale, 1.0, 1.18);
        let rw = self.render_width;
        let rh = self.render_height;
        self.pool.install(|| {
            packed
                .par_chunks_mut(self.width * 2)
                .enumerate()
                .for_each(|(py, row)| {
                    let source_y = if self.height <= 1 {
                        0.0
                    } else {
                        py as f32 * (rh as f32 - 1.0) / (self.height as f32 - 1.0)
                    };
                    for px in 0..self.width {
                        let source_x = if self.width <= 1 {
                            0.0
                        } else {
                            px as f32 * (rw as f32 - 1.0) / (self.width as f32 - 1.0)
                        };
                        let coverage = clamp(
                            mask_radius + 0.5 - self.full_distance[py * self.width + px],
                            0.0,
                            1.0,
                        );
                        let mut rgb = [0.0_f32; 3];

                        if coverage > 0.0 {
                            rgb = sample_bicubic(&fluid, rw, rh, source_x, source_y);
                            // Recreate paper tooth at native LCD resolution.
                            // Generating it in the reduced fluid buffer would
                            // turn these pigment flecks into large blurry dots.
                            let screen_x = px as f32;
                            let screen_y = py as f32;
                            let paper = noise(screen_x * 0.43 + 19.7, screen_y * 0.43 + 71.3) - 0.5;
                            let fiber = noise(
                                (screen_x + screen_y * 0.22) * 0.075 + 113.0,
                                (screen_y - screen_x * 0.08) * 0.62 + 37.0,
                            ) - 0.5;
                            let tooth = paper * 7.0 + fiber * 5.0;
                            rgb[0] += tooth * 0.72;
                            rgb[1] += tooth * 0.92;
                            rgb[2] += tooth;

                            // Pull sparse dark pigment a few pixels beyond a
                            // colour boundary. This is the short, directional
                            // brush-hair detail visible in the original UI.
                            let trail = sample_bilinear(
                                &fluid,
                                rw,
                                rh,
                                (source_x - 1.35).max(0.0),
                                (source_y + 1.05).min(rh as f32 - 1.0),
                            );
                            let pigment_delta = clamp(
                                (luminance(rgb) - luminance(trail)) / 58.0,
                                0.0,
                                1.0,
                            );
                            let broken_edge = smoothstep(
                                0.46,
                                0.82,
                                noise(screen_x * 0.16 + 241.0, screen_y * 0.83 + 59.0),
                            );
                            let bristle = pigment_delta * broken_edge * 0.46;
                            for channel in 0..3 {
                                rgb[channel] = mix(rgb[channel], trail[channel], bristle);
                            }
                            for channel in &mut rgb {
                                *channel *= coverage;
                            }
                        }
                        if let Some(layer) = overlay {
                            let overlay_index = (py * self.width + px) * 4;
                            let alpha = layer[overlay_index + 3] as f32 / 255.0;
                            if alpha > 0.0 {
                                for channel in 0..3 {
                                    rgb[channel] = mix(
                                        rgb[channel],
                                        layer[overlay_index + channel] as f32,
                                        alpha,
                                    );
                                }
                            }
                        }
                        let r = clamp(rgb[0], 0.0, 255.0) as u16;
                        let g = clamp(rgb[1], 0.0, 255.0) as u16;
                        let b = clamp(rgb[2], 0.0, 255.0) as u16;
                        let value = ((r & 0xf8) << 8) | ((g & 0xfc) << 3) | (b >> 3);
                        row[px * 2] = (value >> 8) as u8;
                        row[px * 2 + 1] = value as u8;
                    }
                });
        });
        packed
    }
}

#[pymodule]
fn _watercolor_rust(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OrbRenderer>()?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

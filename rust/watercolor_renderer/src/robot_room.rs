//! Cutaway room: choose the two far walls in world space, not screen space.
//! Everything here shares the static-scene cache and ordinary geometry depth.
use super::Surface;

fn far_sides((sin, cos): (f32, f32)) -> (f32, f32) {
    // Inverse-rotate the camera's ground direction (+X, -Z).
    let opposite = |v: f32| if v >= 0.0 { -1.0 } else { 1.0 };
    (opposite(cos - sin), opposite(-sin - cos))
}

pub(super) fn draw(s: &mut Surface) {
    let (x_side, z_side) = far_sides(s.orbit);
    s.block([0.0, -0.24, 0.0], [4.8, 0.30, 4.4], [78, 88, 89]);
    // A border of wooden floorboards surrounds the existing workstation rug/tile.
    for x in 0..8 {
        s.block(
            [-2.09 + x as f32 * 0.60, -0.065, 0.0],
            [0.58, 0.06, 4.34],
            if x % 2 == 0 {
                [159, 139, 111]
            } else {
                [172, 151, 120]
            },
        );
    }
    // Keep front walls AND their attached furniture out of the cutaway view.
    // Wall coordinates remain fixed in the room as the camera moves.
    wall(s, true, x_side);
    wall(s, false, z_side);
}

fn wall(s: &mut Surface, x_wall: bool, side: f32) {
    let mut block = |u: f32, y: f32, inward: f32, w: f32, h: f32, d: f32, color| {
        let edge = if x_wall { 2.34 } else { 2.14 };
        let normal = side * (edge - inward);
        if x_wall {
            s.block([normal, y, u], [d, h, w], color);
        } else {
            s.block([u, y, normal], [w, h, d], color);
        }
    };
    let width = if x_wall { 4.4 } else { 4.8 };
    let paint = if x_wall {
        [133, 159, 165]
    } else {
        [176, 190, 181]
    };
    block(0.0, 1.43, 0.0, width, 2.96, 0.12, paint);
    block(0.0, 0.13, 0.09, width, 0.22, 0.06, [93, 117, 119]);
    block(0.0, 2.92, 0.0, width + 0.04, 0.09, 0.16, [204, 212, 197]);
    if x_wall {
        // Recessed blue window, sill and crossbars, on either X wall.
        block(0.30, 2.01, 0.075, 1.65, 1.26, 0.08, [219, 221, 202]);
        block(0.30, 2.01, 0.125, 1.44, 1.04, 0.025, [77, 132, 157]);
        block(0.30, 2.23, 0.145, 1.42, 0.54, 0.02, [128, 190, 206]);
        block(0.30, 2.01, 0.17, 0.055, 1.08, 0.04, [218, 224, 211]);
        block(0.30, 2.01, 0.17, 1.46, 0.055, 0.04, [218, 224, 211]);
        block(0.30, 1.34, 0.18, 1.82, 0.10, 0.34, [197, 164, 122]);
        // Low bookcase at the side of the workstation, clear of the robot.
        block(-0.40, 0.48, 0.33, 1.45, 0.84, 0.52, [102, 99, 82]);
        block(-0.40, 0.90, 0.34, 1.55, 0.09, 0.59, [195, 157, 110]);
        for i in 0..6 {
            block(
                -0.96 + i as f32 * 0.21,
                0.52,
                0.62,
                0.15,
                0.43 + (i % 3) as f32 * 0.05,
                0.06,
                [[91, 139, 145], [181, 127, 89], [189, 178, 125]][i % 3],
            );
        }
    } else {
        // Pinboard and a shelf of books on the perpendicular wall.
        block(-0.60, 2.13, 0.09, 1.36, 0.94, 0.09, [120, 99, 76]);
        block(-0.60, 2.13, 0.15, 1.22, 0.80, 0.04, [193, 161, 114]);
        for (u, y, color) in [
            (-0.87, 2.28, [231, 222, 183]),
            (-0.35, 2.17, [157, 192, 186]),
            (-0.78, 1.97, [221, 190, 144]),
        ] {
            block(u, y, 0.18, 0.32, 0.23, 0.02, color);
        }
        block(1.10, 1.88, 0.20, 0.96, 0.10, 0.40, [192, 152, 109]);
        for i in 0..4 {
            block(
                0.79 + i as f32 * 0.19,
                2.11,
                0.18,
                0.13,
                0.36,
                0.20,
                [[111, 151, 152], [210, 181, 127]][i % 2],
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_far_walls_for_every_camera_heading() {
        for i in 0..360 {
            let (sin, cos) = (i as f32 * std::f32::consts::PI / 180.0).sin_cos();
            let (x, z) = far_sides((sin, cos));
            assert!(x * (cos - sin) <= 0.0);
            assert!(z * (-sin - cos) <= 0.0);
        }
        for (i, expected) in [(-1.0, 1.0), (1.0, 1.0), (1.0, -1.0), (-1.0, -1.0)]
            .iter()
            .enumerate()
        {
            assert_eq!(
                far_sides((i as f32 * std::f32::consts::FRAC_PI_2).sin_cos()),
                *expected
            );
        }
    }
}

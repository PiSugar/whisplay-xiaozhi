# Linux AArch64 prebuilt

- Built on Raspberry Pi CM5, 2026-09-21, using `tools/build_watercolor_rust.sh`.
- Rust/Cargo 1.97.1; Debian glibc 2.41; Python 3.13.5.
- Generic AArch64 target (no `target-cpu=native`); PyO3 stable ABI, Python 3.9+.
- Requires glibc 2.35 or newer (highest GLIBC symbol version in this binary).
- Exports `OrbRenderer` and `RobotRenderer` from the same `_watercolor_rust` module.
- SHA-256: `4f7c9d06adc03b15992ab53712c0b06b1ec1ed93e47db3bd0cb26ec017511188`.
- Rebuilt 2026-09-22 on the same CM5: idle arcade gaming alternates with naps,
  yields to real activity, and uses the laptop screen and existing fixed-length arms.
- Adds random viewer-facing waves and left-wrist display checks, mutually exclusive
  with other idle gestures and smoothly interrupted by activity.
- Includes eased quarter-turn camera orbit, raised billboard sleep Z, randomized
  working scan/thinking/drinking, idle foot swings/stretching, and left-hand-only desk sleep.
  Camera-motion rendering uses backface culling and incremental edge rasterization.

Rebuild on a compatible Linux AArch64 host with Rust, Python development headers,
and the complete `rust/watercolor_renderer/assets/` directory present:

```sh
PYO3_PYTHON=/usr/bin/python3 bash tools/build_watercolor_rust.sh
```

Copy the archived `.so` into `display/_watercolor_rust.so` on deployment and
restart the application. Do not use the macOS preview binary on a Raspberry Pi.
Update this provenance and checksum whenever replacing the prebuilt.

## Validation (2026-09-21)

- CM5: all 19 Rust unit tests passed, including game/nap alternation and wakeup,
  non-overlapping idle stretching,
  smooth interruption, and occurrence before the default sleep threshold.
- Deployment target `192.168.100.175` (AArch64, Python 3.13.5, glibc 2.41):
  14 robot integration tests and 9 button/application-state tests passed.
- Initial robot build: forced fire and rain events through the daemon framebuffer:
  29.6 / 29.9 delivered frames per second at a 30 FPS target. Render plus
  framebuffer-write latency: median 19.33 ms, p95 23.77 ms.
- These timings measure submitted frames, not physical LCD scan-out or full
  concurrent voice-session performance.

Pre-stretch baseline, `.175`, 30 FPS target, through the daemon framebuffer:

| Scene | Delivered FPS | Render + framebuffer write p95 |
| --- | ---: | ---: |
| Idle / foot swings | 29.7 | 11.96 ms |
| Drink | 29.9 | 13.20 ms |
| Think | 29.8 | 13.23 ms |
| Left-hand desk sleep | 29.9 | 10.63 ms |
| Four 90-degree orbits | 29.8 | 21.74 ms |
| Edge-to-edge photo card | 29.7 | 16.37 ms |

Before incremental rasterization/backface culling, the orbit test reached only
18.7 FPS (p95 72.5 ms). Screenshots of sleeping, drinking and photo card were
inspected. Button hold/short-click/double-click routing is covered by application
tests; the benchmark requests rotations through the display-thread queue.

Stretch update: 16 Rust tests on CM5 and 14 robot integration tests on `.175`
passed. A 20-second idle/stretch playback through the device framebuffer reached
29.8 FPS, with render/write p95 13.26 ms. The previous timing table is retained
as the pre-stretch performance baseline.

Arcade update (2026-09-22): 18 Rust tests on CM5 and 14 integration tests on
`.175` passed. A seeded idle game rendered through the device framebuffer at
29.6 FPS (render/write p95 13.25 ms). The first idle choice is seeded random;
subsequent game and nap phases alternate, and real activity cancels the pastime.

Wave/wrist update (2026-09-22): 19 Rust tests on CM5 and 14 integration tests on
`.175` passed. Seeded native-framebuffer playback reached 29.7 FPS waving and
29.9 FPS checking the wrist display (render/write p95 8.31 / 8.32 ms, without
caption compositing). The random scheduler excludes simultaneous stretching,
foot swings and idle glances, and fades the gestures out on activity.

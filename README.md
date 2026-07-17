# MLF CoinDetector

**Proof of concept** running on an **Arduino UNO Q** with the **Arduino App Lab**. From a USB camera feed it:

1. **detects and identifies colored pucks** ("palets") with an [Edge Impulse](https://edgeimpulse.com/) model,
2. **localizes each puck in millimeters** on a calibrated plane (≈ ±1 mm),
3. points a **servo "arrow"** at the detected puck, and
4. drives a **TurtleBot3 Waffle** where the puck acts as a **joystick**.

The long-term goal is a robot arm picking pucks off a conveyor; this POC covers perception, millimeter
localization, and a validated mobile-robot teleop link.

> ⚠️ **Status: proof of concept.** Started from the Arduino App Lab _"Detect Objects on Camera"_ example
> and heavily extended. Some web-UI leftovers from the original example may remain.

## Pipeline

```
USB camera ─► video_object_detection (Edge Impulse FOMO) ─► python/main.py
                                                              ├─ OpenCV sub-pixel refine ─► 4-point homography ─► (X, Y) in mm
                                                              ├─ Web UI  (detections, calibration, servo config)
                                                              ├─ Servo "arrow"  ──Bridge/RPC──►  MCU (pin D3)  ─► points at the puck
                                                              └─ Puck = joystick ──UDP {jx,jy}──► ROS2 node ─► /cmd_vel ─► TurtleBot3 Waffle
```

## Features

- **Detection** — Edge Impulse **FOMO** object detection on the board via the `video_object_detection` Brick.
- **Millimeter localization** — hybrid approach: FOMO gives the coarse cell, **OpenCV** refines the puck
  center at sub-pixel precision (HSV color segmentation, color auto-sampled so it is BGR/RGB agnostic), and a
  **4-point homography** maps pixels → millimeters. Reaches **≈ ±1 mm** (center ≈ ±0.5 mm).
- **Calibration (web UI)** — two modes, persisted to `calibration.json`:
  - **4-corner click** on a flat target square (174 mm), and
  - **puck-based** calibration (place the puck at each corner) which **cancels parallax** — the camera is
    **eye-in-hand** (mounted on the arm tool), measured from a fixed, repeatable observation pose.
- **Servo pointer** — a servo arrow (signal on **D3**, driven by the MCU over the `Bridge` RPC) always points
  at the detected puck. Angle computed relative to the **field center** (robust to servo placement), **EMA
  smoothed**; servo position configurable in the UI, persisted to `servo_config.json`.
- **ROS2 Waffle teleop** — the puck's position relative to the field center is sent as a `{jx, jy}` joystick
  over **UDP**; a ROS2 node maps it to `/cmd_vel` and drives a **TurtleBot3 Waffle**. Validated end-to-end.

## Repository layout

| Path | What |
|---|---|
| [`python/main.py`](python/main.py) | App backend: detection, refinement, homography, calibration, servo, ROS UDP |
| [`python/requirements.txt`](python/requirements.txt) | Python deps installed in the App Lab container (`numpy`, `opencv-python-headless`) |
| [`assets/`](assets/) | Web UI (`index.html`, `app.js`): live detections, calibration canvas, servo panel |
| [`sketch/`](sketch/) | MCU sketch (Zephyr) driving the pointer servo on D3 (pins `Servo (1.3.0)` library) |
| [`ros2/mlf_coin_teleop/`](ros2/mlf_coin_teleop/) | ROS2 package (`joystick_teleop` node) — UDP `{jx,jy}` → `/cmd_vel` |
| [`ros2/README.md`](ros2/README.md) | ROS2 package doc (build, params) |
| [`WAFFLE_RUNBOOK.md`](WAFFLE_RUNBOOK.md) | Full Waffle operating runbook (IP, SSH, bringup, node, troubleshooting, from-scratch reinstall) |

`calibration.json` and `servo_config.json` are **board-specific and git-ignored**.

## Model

The detection model is trained and exported from **Edge Impulse**, then referenced in [`app.yaml`](app.yaml):

```yaml
bricks:
  - arduino:video_object_detection:
      model: ei-model-1054574-2
  - arduino:web_ui: {}
```

FOMO quantizes the centroid to a grid cell (≈ 40 px on 640×480), which is why localization adds the OpenCV
sub-pixel refinement on top. A **160×160** input resolution (retrained with corner/edge images) detects across
the whole field; 96×96 was too coarse. The homography/refinement work on the 640×480 image and are
**independent of the model resolution** (no recalibration needed when swapping models).

## How the ROS2 teleop works

The puck acts as a joystick. `python/main.py` normalizes the puck position against the field center and sends
a UDP datagram to the Pi running the ROS2 node:

- payload: JSON `{"jx": <-1..1>, "jy": <-1..1>}` on **UDP port 5005**
- `jy > 0` → robot **forward**, `jx > 0` → **turn right** (Waffle is differential; a diagonal = drive + turn)
- **safety**: no packet for `0.5 s` → the node publishes a zero `Twist` (robot stops)

Set in `python/main.py`: `WAFFLE_HOST` (the Pi's IP), and per-axis `JOY_INVERT_X` / `JOY_INVERT_Y` toggles.
See [`WAFFLE_RUNBOOK.md`](WAFFLE_RUNBOOK.md) to bring up the robot and [`ros2/README.md`](ros2/README.md) for
the node.

## Hardware and software requirements

### Hardware
- [Arduino® UNO Q](https://store.arduino.cc/products/uno-q) (or Arduino VENTUNO Q)
- USB camera (720p used here at 640×480; 1080p also available), mounted eye-in-hand
- USB-C® hub with external power (5 V, 3 A) _(UNO Q only)_
- Calibration target: a **174 mm square** with 4 markers (a Niryo calibration board was used)
- A test puck (label `redCoin`, ⌀32 mm × 10 mm)
- **Servo** (arrow pointer) on pin D3
- **TurtleBot3 Waffle** _(for the ROS2 teleop stage)_
- A personal computer with internet access

### Software
- Arduino App Lab (run the App in **Network Mode** — it needs the USB hub + camera)
- ROS2 **Humble** on the Waffle's Raspberry Pi (already set up — see [`WAFFLE_RUNBOOK.md`](WAFFLE_RUNBOOK.md))

## How to use

1. Connect the USB-C hub to the UNO Q and plug the USB camera into the hub; attach the external power supply.
2. Run the App from the Arduino App Lab. It opens in the browser (or open `<board-name>.local:7000`).
3. Present colored pucks in front of the camera — detections update live.
4. **Calibrate** from the web UI (4-corner click, or the puck-based mode to cancel parallax) to get positions in mm.
5. _(optional)_ Configure the **servo** position in the UI so the arrow points at the puck.
6. _(optional)_ **Waffle teleop**: set `WAFFLE_HOST` to the Pi's IP, start the robot (see the runbook), and the
   puck drives the robot. ⚠️ Wheels up and low speeds first.

## Backend at a glance — [`python/main.py`](python/main.py)

```python
ui = WebUI()
cam = Camera()  # shared: fed to detection and reused for calibration
detection_stream = VideoObjectDetection(camera=cam, confidence=0.5, debounce_sec=0.0, camera_preview=True)
```

For each detection, the backend refines the center (`refine_center`), maps it to mm (`_pixel_to_mm` via the
homography), pushes it to the UI, aims the servo (`compute_servo_angle` → Bridge to the MCU), and — if
enabled — sends the joystick over UDP (`_send_joystick`). Calibration and servo settings are handled through
`ui.on_message` callbacks and persisted to JSON.

## Roadmap

- [x] Detect colored pucks with an Edge Impulse model on the UNO Q
- [x] Real-time visualization in the web UI
- [x] Extract the position of the pucks in **millimeters** (homography + OpenCV refinement)
- [x] Web-UI calibration (4-corner + puck-based, parallax-corrected)
- [x] Servo "arrow" pointing at the detected puck
- [x] Drive a **TurtleBot3 Waffle** with the puck as a joystick (UDP → `/cmd_vel`)
- [ ] Output positions directly in the **robot frame** (define the 4 corners in robot coordinates)
- [ ] Real pick-up by a robot **arm** on the conveyor
- [ ] Fully clean up the web UI (remove leftovers from the example)

## Credits

Based on the Arduino App Lab _"Detect Objects on Camera"_ example. Detection model trained with Edge Impulse.

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import Camera
from datetime import datetime, UTC

import os
import json
import base64
import cv2
import numpy as np

log = Logger("localisation")

ui = WebUI()

# Caméra partagée : passée au brick de détection, et réutilisée pour capturer
# des images de calibration.
cam = Camera()
detection_stream = VideoObjectDetection(camera=cam, confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# État de calibration (homographie pixel -> mm)
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")
H_current = None        # np.ndarray 3x3 ou None
square_mm_current = None


# ---------------------------------------------------------------------------
# Détection (existant) + diagnostic bbox
# ---------------------------------------------------------------------------
def send_detections_to_ui(detections: dict, frame=None):
  for key, values in detections.items():
    for value in values:
      bbox = value.get("bounding_box_xyxy")
      log.info(f"[diag] label={key} bbox_xyxy={bbox} conf={value.get('confidence')}")
      entry = {
        "content": key,
        "confidence": value.get("confidence"),
        "bbox": bbox,
        "timestamp": datetime.now(UTC).isoformat()
      }
      ui.send_message("detection", message=entry)

detection_stream.on_detect_all(send_detections_to_ui)


# ---------------------------------------------------------------------------
# Calibration — capture d'image
# ---------------------------------------------------------------------------
def _grab_frame():
  """Retourne (image BGR numpy, source) ou (None, None)."""
  try:
    frame = cam.capture()
    if frame is not None:
      return frame, "camera.capture"
  except Exception as e:
    log.warning(f"[calib] cam.capture() a échoué: {e}")
  raw = getattr(detection_stream, "_last_camera_frame", None)
  if raw:
    try:
      b64 = raw.split(",", 1)[1] if "," in raw else raw
      arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
      img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
      if img is not None:
        return img, "preview_buffer"
    except Exception as e:
      log.warning(f"[calib] repli preview a échoué: {e}")
  return None, None


def on_calib_capture(sid, payload=None):
  frame, source = _grab_frame()
  if frame is None:
    ui.send_message("calib_frame", message={"ok": False, "error": "Aucune image disponible (caméra pas prête ?)"})
    return
  h, w = int(frame.shape[0]), int(frame.shape[1])
  ok, buf = cv2.imencode(".jpg", frame)
  if not ok:
    ui.send_message("calib_frame", message={"ok": False, "error": "Encodage JPEG échoué"})
    return
  b64 = base64.b64encode(buf.tobytes()).decode("ascii")
  log.info(f"[calib] image capturée {w}x{h} (source={source})")
  ui.send_message("calib_frame", message={"ok": True, "w": w, "h": h, "source": source,
                                          "img": "data:image/jpeg;base64," + b64})


# ---------------------------------------------------------------------------
# Calibration — homographie 4 points (pixel -> mm) + test
# ---------------------------------------------------------------------------
def _world_corners(square_mm):
  # ordre attendu : haut-gauche, haut-droit, bas-droit, bas-gauche
  return np.array([[0, 0], [square_mm, 0], [square_mm, square_mm], [0, square_mm]], dtype=np.float32)


def _load_calibration():
  global H_current, square_mm_current
  try:
    if os.path.exists(CALIB_PATH):
      with open(CALIB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
      H_current = np.array(data["H"], dtype=np.float64)
      square_mm_current = data.get("square_mm")
      log.info(f"[calib] calibration chargée depuis {CALIB_PATH} (carré={square_mm_current} mm)")
  except Exception as e:
    log.error(f"[calib] échec chargement calibration: {e}")


def on_calib_compute(sid, payload):
  global H_current, square_mm_current
  try:
    points = payload.get("points") if isinstance(payload, dict) else None
    square_mm = float(payload.get("square_mm", 174)) if isinstance(payload, dict) else 174.0
    if not points or len(points) != 4:
      ui.send_message("calib_result", message={"ok": False, "error": "Il faut exactement 4 points."})
      return
    src = np.array(points, dtype=np.float32)
    dst = _world_corners(square_mm)
    H = cv2.getPerspectiveTransform(src, dst)
    # erreur de reprojection : on remappe les 4 pixels cliqués et on compare aux coins réels
    mapped = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = float(np.max(np.linalg.norm(mapped - dst, axis=1)))
    data = {
      "H": H.tolist(),
      "square_mm": square_mm,
      "points_px": points,
      "image_size": [640, 480],
      "created": datetime.now(UTC).isoformat(),
    }
    try:
      with open(CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    except Exception as e:
      log.error(f"[calib] écriture {CALIB_PATH} impossible: {e}")
      ui.send_message("calib_result", message={"ok": False, "error": f"Écriture impossible: {e}"})
      return
    H_current = H.astype(np.float64)
    square_mm_current = square_mm
    log.info(f"[calib] homographie enregistrée ({CALIB_PATH}), erreur repro max={err:.2f} mm")
    ui.send_message("calib_result", message={"ok": True, "error_mm": round(err, 2), "path": CALIB_PATH})
  except Exception as e:
    log.error(f"[calib] calcul homographie échoué: {e}")
    ui.send_message("calib_result", message={"ok": False, "error": str(e)})


def on_calib_test_point(sid, payload):
  if H_current is None:
    ui.send_message("calib_test_result", message={"ok": False, "error": "Pas de calibration."})
    return
  try:
    u = float(payload.get("u"))
    v = float(payload.get("v"))
    pt = np.array([[[u, v]]], dtype=np.float64)
    xy = cv2.perspectiveTransform(pt, H_current).reshape(2)
    ui.send_message("calib_test_result", message={"ok": True, "u": u, "v": v,
                                                  "X": round(float(xy[0]), 1), "Y": round(float(xy[1]), 1)})
  except Exception as e:
    ui.send_message("calib_test_result", message={"ok": False, "error": str(e)})


ui.on_message("calib_capture", on_calib_capture)
ui.on_message("calib_compute", on_calib_compute)
ui.on_message("calib_test_point", on_calib_test_point)

_load_calibration()

App.run()

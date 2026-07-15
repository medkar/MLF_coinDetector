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

# Caméra partagée : passée au brick de détection, et réutilisée pour la calibration.
cam = Camera()
detection_stream = VideoObjectDetection(camera=cam, confidence=0.5, debounce_sec=0.0, camera_preview=True)

def on_override_threshold(sid, threshold):
  # Au démarrage, l'UI peut envoyer le seuil avant que le runner du modèle soit
  # prêt (ws:4912) -> on ignore proprement au lieu de logguer une stack trace.
  try:
    detection_stream.override_threshold(threshold)
  except Exception as e:
    log.warning(f"[detection] override_threshold ignoré (runner pas prêt ?): {e}")

ui.on_message("override_th", on_override_threshold)

# État de calibration (homographie pixel -> mm)
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")
H_current = None        # np.ndarray 3x3 ou None
square_mm_current = None

# --- Paramètres de l'affinage OpenCV (ajustables d'après les logs) ---
REFINE_ROI = 70   # demi-fenêtre d'analyse autour du centre FOMO (px)
HUE_TOL = 15      # tolérance de teinte (OpenCV: 0-179)
SAT_MIN = 60      # saturation minimale d'un pixel "coloré"
VAL_MIN = 40      # valeur (luminosité) minimale
MIN_AREA = 20     # aire minimale du contour retenu (px²)


# ---------------------------------------------------------------------------
# Affinage : centre sub-pixel du disque coloré autour de la cellule FOMO
# ---------------------------------------------------------------------------
def refine_center(frame_bgr, bbox):
  """Retourne (u, v) précis dans l'image, ou (None, None) si échec.

  On échantillonne la couleur au centre de la cellule FOMO, puis on segmente
  le disque par proximité de teinte et on prend le centroïde du plus grand
  contour. Insensible à l'ordre BGR/RGB (comparaison de teinte interne).
  """
  x1, y1, x2, y2 = bbox
  cu = (x1 + x2) / 2.0
  cv_ = (y1 + y2) / 2.0
  h, w = frame_bgr.shape[:2]
  x0 = int(max(0, cu - REFINE_ROI)); xe = int(min(w, cu + REFINE_ROI))
  y0 = int(max(0, cv_ - REFINE_ROI)); ye = int(min(h, cv_ + REFINE_ROI))
  roi = frame_bgr[y0:ye, x0:xe]
  if roi.size == 0:
    return None, None

  hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
  # graine : teinte médiane d'un petit patch au centre de la ROI
  pcx = roi.shape[1] // 2
  pcy = roi.shape[0] // 2
  patch = hsv[max(0, pcy - 3):pcy + 4, max(0, pcx - 3):pcx + 4].reshape(-1, 3)
  seed_h = float(np.median(patch[:, 0]))

  hue = hsv[:, :, 0].astype(np.int16)
  dh = np.abs(hue - seed_h)
  dh = np.minimum(dh, 180 - dh)  # distance circulaire de teinte
  mask = ((dh <= HUE_TOL) & (hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN))
  mask = (mask.astype(np.uint8)) * 255
  mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

  cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
  if not cnts:
    return None, None
  c = max(cnts, key=cv2.contourArea)
  if cv2.contourArea(c) < MIN_AREA:
    return None, None
  m = cv2.moments(c)
  if m["m00"] == 0:
    return None, None
  ru = m["m10"] / m["m00"]
  rv = m["m01"] / m["m00"]
  return x0 + ru, y0 + rv


def _pixel_to_mm(u, v):
  if H_current is None or u is None:
    return None, None
  pt = np.array([[[float(u), float(v)]]], dtype=np.float64)
  xy = cv2.perspectiveTransform(pt, H_current).reshape(2)
  return round(float(xy[0]), 1), round(float(xy[1]), 1)


# ---------------------------------------------------------------------------
# Détection -> affinage -> localisation (X, Y) mm -> UI
# ---------------------------------------------------------------------------
def send_detections_to_ui(detections: dict, frame=None):
  img = None
  if frame is not None:
    try:
      img = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
    except Exception as e:
      log.warning(f"[loc] décodage image échoué: {e}")

  for key, values in detections.items():
    for value in values:
      bbox = value.get("bounding_box_xyxy")
      u = v = None
      refined = False
      if img is not None and bbox is not None:
        try:
          u, v = refine_center(img, bbox)
          refined = u is not None
        except Exception as e:
          log.warning(f"[loc] affinage échoué: {e}")
      if u is None and bbox is not None:  # repli : centre de la cellule FOMO
        u = (bbox[0] + bbox[2]) / 2.0
        v = (bbox[1] + bbox[3]) / 2.0

      X, Y = _pixel_to_mm(u, v)
      upx = None if u is None else round(u, 1)
      vpx = None if v is None else round(v, 1)
      log.info(f"[loc] label={key} conf={value.get('confidence'):.2f} px=({upx},{vpx}) affiné={refined} mm=({X},{Y})")

      entry = {
        "content": key,
        "confidence": value.get("confidence"),
        "bbox": bbox,
        "u": upx, "v": vpx,
        "X": X, "Y": Y,
        "refined": refined,
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
    X, Y = _pixel_to_mm(u, v)
    ui.send_message("calib_test_result", message={"ok": True, "u": u, "v": v, "X": X, "Y": Y})
  except Exception as e:
    ui.send_message("calib_test_result", message={"ok": False, "error": str(e)})


ui.on_message("calib_capture", on_calib_capture)
ui.on_message("calib_compute", on_calib_compute)
ui.on_message("calib_test_point", on_calib_test_point)

_load_calibration()

App.run()

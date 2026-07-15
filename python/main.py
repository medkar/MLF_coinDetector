# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App, Bridge, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import Camera
from datetime import datetime, UTC

import os
import time
import json
import math
import base64
import cv2
import numpy as np

log = Logger("localisation")

ui = WebUI()

# Caméra partagée : passée au brick de détection, et réutilisée pour la calibration.
cam = Camera()
detection_stream = VideoObjectDetection(camera=cam, confidence=0.5, debounce_sec=0.0, camera_preview=True)


def on_override_threshold(sid, threshold):
  try:
    detection_stream.override_threshold(threshold)
  except Exception as e:
    log.warning(f"[detection] override_threshold ignoré (runner pas prêt ?): {e}")

ui.on_message("override_th", on_override_threshold)

# --- État de calibration (homographie pixel -> mm) ---
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")
H_current = None        # np.ndarray 3x3 ou None
square_mm_current = None

# Dernière détection affinée (pour la calibration par palet)
_last_refined = {"u": None, "v": None, "t": 0.0, "label": None, "refined": False}

# --- Servo "flèche" qui pointe vers le palet (piloté via le MCU / Bridge) ---
SERVO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servo_config.json")
# Position du servo dans le repère mm de la mire (par défaut : milieu du bord inférieur).
SERVO = {"x": 87.0, "y": 174.0, "offset": 0.0, "invert": False, "enabled": True}
_servo_state = {"angle": None, "t": 0.0}
_servo_smooth = {"angle": None}          # angle lissé (EMA)
MIN_SERVO_INTERVAL = 0.05  # le pont MCU n'a pas de file d'attente : ~20 cmd/s max
SERVO_SMOOTH_ALPHA = 0.4   # lissage EMA (plus petit = plus lisse mais plus lent)
SERVO_DEADBAND = 2         # ne commande pas le servo pour un changement < ce seuil (°)

# --- Paramètres de l'affinage OpenCV (ajustables d'après les logs) ---
REFINE_ROI = 70   # demi-fenêtre d'analyse autour du centre FOMO (px)
HUE_TOL = 15      # tolérance de teinte (OpenCV: 0-179)
SAT_MIN = 60      # saturation minimale d'un pixel "coloré"
VAL_MIN = 40      # valeur (luminosité) minimale
MIN_AREA = 20     # aire minimale du contour retenu (px²)

# --- Détection auto des cibles concentriques de la mire (ajustable) ---
TARGET_MIN_AREA = 15       # aire mini d'un anneau (px²)
TARGET_MAX_AREA = 20000    # aire maxi (exclut le contour du plateau)
TARGET_CLUSTER_TOL = 12    # px : regroupement des contours concentriques (même centre)
TARGET_MIN_RINGS = 3       # nb mini de contours empilés pour valider une cible


# ---------------------------------------------------------------------------
# Affinage : centre sub-pixel du disque coloré autour de la cellule FOMO
# ---------------------------------------------------------------------------
def refine_center(frame_bgr, bbox):
  """Retourne (u, v) précis dans l'image, ou (None, None) si échec."""
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
  pcx = roi.shape[1] // 2
  pcy = roi.shape[0] // 2
  patch = hsv[max(0, pcy - 3):pcy + 4, max(0, pcx - 3):pcx + 4].reshape(-1, 3)
  seed_h = float(np.median(patch[:, 0]))

  hue = hsv[:, :, 0].astype(np.int16)
  dh = np.abs(hue - seed_h)
  dh = np.minimum(dh, 180 - dh)
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
  return x0 + m["m10"] / m["m00"], y0 + m["m01"] / m["m00"]


def _pixel_to_mm(u, v):
  if H_current is None or u is None:
    return None, None
  pt = np.array([[[float(u), float(v)]]], dtype=np.float64)
  xy = cv2.perspectiveTransform(pt, H_current).reshape(2)
  return round(float(xy[0]), 1), round(float(xy[1]), 1)


# ---------------------------------------------------------------------------
# Servo : angle de pointage vers le palet + envoi au MCU (débit limité)
# ---------------------------------------------------------------------------
def _load_servo_config():
  try:
    if os.path.exists(SERVO_PATH):
      with open(SERVO_PATH, "r", encoding="utf-8") as f:
        SERVO.update(json.load(f))
      log.info(f"[servo] config chargée: {SERVO}")
  except Exception as e:
    log.error(f"[servo] chargement config échoué: {e}")


def _save_servo_config():
  try:
    with open(SERVO_PATH, "w", encoding="utf-8") as f:
      json.dump(SERVO, f, indent=2)
  except Exception as e:
    log.error(f"[servo] sauvegarde config échouée: {e}")


def compute_servo_angle(xp, yp):
  # Angle robuste quelle que soit la position du servo (milieu d'un bord) :
  # on mesure la direction vers le palet RELATIVEMENT à la direction "vers le
  # centre de la mire" (= 90°, la flèche pointe alors au milieu du terrain),
  # avec repli à ±180°. offset/invert servent au calage physique.
  sq = float(square_mm_current or 174.0)
  cx = cy = sq / 2.0
  forward = math.atan2(cy - SERVO["y"], cx - SERVO["x"])
  base = math.atan2(yp - SERVO["y"], xp - SERVO["x"])
  rel = math.degrees(base - forward)
  rel = ((rel + 180.0) % 360.0) - 180.0          # repli dans (-180, 180]
  ang = 90.0 + SERVO["offset"] + (-rel if SERVO["invert"] else rel)
  return int(max(0, min(180, round(ang))))


def _servo_send(angle, force=False):
  now = time.time()
  if not force and _servo_state["angle"] is not None \
     and abs(angle - _servo_state["angle"]) < 1 and (now - _servo_state["t"]) < 0.5:
    return
  if not force and (now - _servo_state["t"]) < MIN_SERVO_INTERVAL:
    return
  try:
    Bridge.notify("set_servo_angle", int(angle))
    _servo_state["angle"] = angle
    _servo_state["t"] = now
    ui.send_message("servo_state", message={"angle": int(angle)})
  except Exception as e:
    log.warning(f"[servo] Bridge.notify a échoué (MCU/sketch prêt ?): {e}")


def _servo_point(raw_angle):
  """Lisse l'angle (EMA + zone morte) avant de commander le servo -> moins de jitter."""
  s = _servo_smooth["angle"]
  s = float(raw_angle) if s is None else (SERVO_SMOOTH_ALPHA * raw_angle + (1 - SERVO_SMOOTH_ALPHA) * s)
  _servo_smooth["angle"] = s
  target = int(round(s))
  last = _servo_state["angle"]
  if last is not None and abs(target - last) < SERVO_DEADBAND:
    return
  _servo_send(target)


# ---------------------------------------------------------------------------
# Détection -> affinage -> localisation (X,Y) mm -> UI (+ servo)
# ---------------------------------------------------------------------------
def send_detections_to_ui(detections: dict, frame=None):
  img = None
  if frame is not None:
    try:
      img = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
    except Exception as e:
      log.warning(f"[loc] décodage image échoué: {e}")

  best = None  # (confiance, X, Y) du palet le plus sûr -> cible du servo

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

      if u is not None:
        _last_refined.update({"u": float(u), "v": float(v), "t": time.time(), "label": key, "refined": refined})

      X, Y = _pixel_to_mm(u, v)
      if X is not None and Y is not None:
        conf = value.get("confidence") or 0.0
        if best is None or conf > best[0]:
          best = (conf, X, Y)

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

  if SERVO["enabled"] and best is not None:
    _servo_point(compute_servo_angle(best[1], best[2]))

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


def on_calib_get_refined(sid, payload=None):
  # Renvoie la dernière position affinée du palet, pour calibrer avec le palet
  # lui-même (annule la parallaxe : points de calibration à la hauteur du palet).
  if _last_refined["u"] is None or (time.time() - _last_refined["t"]) > 2.0:
    ui.send_message("calib_refined", message={"ok": False,
                    "error": "Aucune détection récente du palet (montre-le immobile au coin)."})
    return
  ui.send_message("calib_refined", message={"ok": True,
                  "u": round(_last_refined["u"], 1), "v": round(_last_refined["v"], 1),
                  "label": _last_refined["label"]})


# ---------------------------------------------------------------------------
# Calibration — détection automatique des cibles concentriques
# ---------------------------------------------------------------------------
def _order_corners(pts):
  a = np.array(pts, dtype=np.float64)
  s = a[:, 0] + a[:, 1]
  d = a[:, 1] - a[:, 0]
  # ordre : haut-gauche, haut-droit, bas-droit, bas-gauche
  return [a[np.argmin(s)].tolist(), a[np.argmin(d)].tolist(),
          a[np.argmax(s)].tolist(), a[np.argmax(d)].tolist()]


def detect_targets(frame_bgr):
  """Détecte les centres des cibles concentriques (bullseyes). Retourne (corners|None, candidates)."""
  gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
  gray = cv2.GaussianBlur(gray, (3, 3), 0)
  thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY_INV, 51, 5)
  cnts, _ = cv2.findContours(thr, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

  # centroïdes des contours de taille plausible
  cents = []
  for c in cnts:
    a = cv2.contourArea(c)
    if a < TARGET_MIN_AREA or a > TARGET_MAX_AREA:
      continue
    m = cv2.moments(c)
    if m["m00"] == 0:
      continue
    cents.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))

  # regroupe les centres proches : une cible = plusieurs anneaux au même centre
  clusters = []  # [somme_x, somme_y, n]
  for (x, y) in cents:
    placed = False
    for cl in clusters:
      cx, cy = cl[0] / cl[2], cl[1] / cl[2]
      if (x - cx) ** 2 + (y - cy) ** 2 <= TARGET_CLUSTER_TOL ** 2:
        cl[0] += x; cl[1] += y; cl[2] += 1
        placed = True
        break
    if not placed:
      clusters.append([x, y, 1])

  markers = [(cl[0] / cl[2], cl[1] / cl[2]) for cl in clusters if cl[2] >= TARGET_MIN_RINGS]
  candidates = [[round(mx, 1), round(my, 1)] for (mx, my) in markers]
  corners = None
  if len(markers) >= 4:
    corners = [[round(c[0], 1), round(c[1], 1)] for c in _order_corners(markers)]
  log.info(f"[calib-auto] contours_plausibles={len(cents)} cibles={len(markers)} corners={'oui' if corners else 'non'}")
  return corners, candidates


def on_calib_auto_detect(sid, payload=None):
  frame, source = _grab_frame()
  if frame is None:
    ui.send_message("calib_auto_result", message={"ok": False, "error": "Aucune image disponible."})
    return
  h, w = int(frame.shape[0]), int(frame.shape[1])
  corners, candidates = detect_targets(frame)
  ok_enc, buf = cv2.imencode(".jpg", frame)
  img = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii") if ok_enc else None
  ui.send_message("calib_auto_result", message={
    "ok": corners is not None,
    "w": w, "h": h, "img": img,
    "corners": corners, "candidates": candidates,
    "error": None if corners is not None else f"{len(candidates)} cible(s) détectée(s) — il en faut 4.",
  })


# ---------------------------------------------------------------------------
# Servo — configuration depuis l'UI + test manuel
# ---------------------------------------------------------------------------
def on_servo_get(sid, payload=None):
  ui.send_message("servo_config", message={"ok": True, **SERVO})


def on_servo_config(sid, payload):
  try:
    if isinstance(payload, dict):
      if "x" in payload: SERVO["x"] = float(payload["x"])
      if "y" in payload: SERVO["y"] = float(payload["y"])
      if "offset" in payload: SERVO["offset"] = float(payload["offset"])
      if "invert" in payload: SERVO["invert"] = bool(payload["invert"])
      if "enabled" in payload: SERVO["enabled"] = bool(payload["enabled"])
    _save_servo_config()
    log.info(f"[servo] config mise à jour: {SERVO}")
    ui.send_message("servo_config", message={"ok": True, **SERVO})
  except Exception as e:
    ui.send_message("servo_config", message={"ok": False, "error": str(e)})


def on_servo_test(sid, payload):
  try:
    angle = int(payload.get("angle")) if isinstance(payload, dict) else int(payload)
    _servo_smooth["angle"] = float(angle)  # resynchronise le lissage
    _servo_send(angle, force=True)
  except Exception as e:
    log.warning(f"[servo] test échoué: {e}")


ui.on_message("calib_capture", on_calib_capture)
ui.on_message("calib_compute", on_calib_compute)
ui.on_message("calib_test_point", on_calib_test_point)
ui.on_message("calib_get_refined", on_calib_get_refined)
ui.on_message("calib_auto_detect", on_calib_auto_detect)
ui.on_message("servo_get", on_servo_get)
ui.on_message("servo_config", on_servo_config)
ui.on_message("servo_test", on_servo_test)

_load_calibration()
_load_servo_config()

App.run()

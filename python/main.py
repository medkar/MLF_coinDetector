# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import Camera
from datetime import datetime, UTC

import base64
import cv2
import numpy as np

log = Logger("localisation")

ui = WebUI()

# Caméra partagée : on la passe au brick de détection et on s'en sert aussi
# pour capturer des images de calibration.
cam = Camera()
# camera_preview=True => le callback reçoit l'image brute (frame=) et le brick
# bufferise la dernière image de preview (repli pour la capture de calibration).
detection_stream = VideoObjectDetection(camera=cam, confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))


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
# Calibration — étape A : capturer une image caméra et l'envoyer à l'UI
# ---------------------------------------------------------------------------
def _grab_frame():
  """Retourne (image BGR numpy, source) ou (None, None)."""
  # 1) tentative directe via le périphérique caméra partagé
  try:
    frame = cam.capture()
    if frame is not None:
      return frame, "camera.capture"
  except Exception as e:
    log.warning(f"[calib] cam.capture() a échoué: {e}")
  # 2) repli : dernière image de preview bufferisée par le brick (dataURL base64)
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

ui.on_message("calib_capture", on_calib_capture)


App.run()

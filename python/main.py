# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from datetime import datetime, UTC

import cv2
import numpy as np

log = Logger("localisation")

ui = WebUI()
# camera_preview=True => le callback reçoit l'image brute (frame=) : utile pour le
# diagnostic ci-dessous et, plus tard, pour la calibration par clic dans l'UI.
detection_stream = VideoObjectDetection(confidence=0.5, debounce_sec=0.0, camera_preview=True)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# Register a callback for when all objects are detected
def send_detections_to_ui(detections: dict, frame=None):
  # --- DIAGNOSTIC (temporaire) : dans quel espace pixel sont les bbox ? ---
  # On décode l'image reçue pour connaître sa taille, et on la compare aux
  # coordonnées de la bbox. Si les bbox dépassent la taille de l'image, elles
  # sont dans un autre repère (résolution du modèle) et il faudra les mettre à l'échelle.
  frame_w = frame_h = None
  if frame is not None:
    img = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
      frame_h, frame_w = img.shape[0], img.shape[1]

  for key, values in detections.items():
    for value in values:
      bbox = value.get("bounding_box_xyxy")
      log.info(f"[diag] label={key} bbox_xyxy={bbox} image(WxH)={frame_w}x{frame_h} conf={value.get('confidence')}")
      entry = {
        "content": key,
        "confidence": value.get("confidence"),
        "bbox": bbox,
        "timestamp": datetime.now(UTC).isoformat()
      }
      ui.send_message("detection", message=entry)

detection_stream.on_detect_all(send_detections_to_ui)

App.run()

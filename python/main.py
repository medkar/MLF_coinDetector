# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import App
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from datetime import datetime, UTC

# --- Contrôle temporaire des dépendances de localisation (non bloquant) ---
# Confirme, dans les logs de l'App Lab, qu'OpenCV/NumPy sont bien installés
# DANS LE CONTENEUR de l'app. À retirer une fois la localisation en place.
try:
  import cv2
  import numpy as np
  print(f"[localisation] OpenCV {cv2.__version__} / NumPy {np.__version__} disponibles", flush=True)
except Exception as e:
  print(f"[localisation] Dépendances manquantes ({e}) — vérifier python/requirements.txt", flush=True)

ui = WebUI()
detection_stream = VideoObjectDetection(confidence=0.5, debounce_sec=0.0)

ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# Register a callback for when all objects are detected
def send_detections_to_ui(detections: dict):
  for key, values in detections.items():
    for value in values:
      entry = {
        "content": key,
        "confidence": value.get("confidence"),
        "timestamp": datetime.now(UTC).isoformat()
      }
      ui.send_message("detection", message=entry)

detection_stream.on_detect_all(send_detections_to_ui)

App.run()

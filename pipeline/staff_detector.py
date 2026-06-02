"""
Staff detector: classifies a person bounding box ROI as staff or customer.
Method: dominant HSV colour analysis — staff wear a uniform-coloured outfit
that falls in a predefined HSV range (loaded from store_layout.json).

This is a rule-based approach. It does not require a trained model and runs at
near-zero cost per frame. A Vision LLM approach was considered for higher accuracy
but ruled out for latency reasons at 15fps — documented in CHOICES.md.
"""

from __future__ import annotations

import json

import cv2
import numpy as np


class StaffDetector:
    def __init__(self, layout_path: str):
        with open(layout_path, encoding="utf-8") as f:
            layout = json.load(f)

        cfg = layout.get("staff_uniform_hsv", {})
        self.hue_min = cfg.get("hue_min", 200)
        self.hue_max = cfg.get("hue_max", 240)
        self.sat_min = cfg.get("saturation_min", 80)
        self.sat_max = cfg.get("saturation_max", 255)
        self.val_min = cfg.get("value_min", 50)
        self.val_max = cfg.get("value_max", 200)
        self.threshold = 0.35

    def is_staff(self, frame: np.ndarray, bbox: tuple[int, int, int, int]) -> bool:
        """
        Returns True if the person ROI matches the staff uniform colour profile.
        bbox: (x1, y1, x2, y2) in pixels.
        """
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return False

        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False

        torso_h = int((y2 - y1) * 0.5)
        torso = roi[int(roi.shape[0] * 0.2):int(roi.shape[0] * 0.2) + torso_h, :]

        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)

        hue_min_cv = int(self.hue_min / 2)
        hue_max_cv = int(self.hue_max / 2)
        lower = np.array([hue_min_cv, self.sat_min, self.val_min], dtype=np.uint8)
        upper = np.array([hue_max_cv, self.sat_max, self.val_max], dtype=np.uint8)

        mask = cv2.inRange(hsv, lower, upper)
        pixel_ratio = np.count_nonzero(mask) / mask.size

        return pixel_ratio >= self.threshold

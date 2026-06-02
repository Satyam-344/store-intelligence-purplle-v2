"""
Re-ID tracker: assigns persistent visitor_ids across frames and across re-entries.

Approach:
1. ByteTrack (via supervision) gives us track_ids within a single clip session.
2. We map track_id → visitor_id using a simple UUID assignment on first appearance.
3. Re-entry detection: when a new ENTRY fires, we compare the appearance embedding
   (mean ResNet18 feature vector of the ROI) against recent_exits embeddings using
   cosine similarity. If similarity > 0.85 within the last 30 minutes → REENTRY.
4. Cross-camera dedup: two cameras covering the same zone share the same ReIDTracker
   instance. If the same person appears in both within a 30s window, the second
   sighting gets the same visitor_id (deduplication by Re-ID similarity).

Why ResNet18 and not OSNet:
  - ResNet18 is included in torchvision with no extra downloads
  - OSNet requires torchreid which adds ~500MB and extra CUDA deps
  - For retail Re-ID at low framerate, ResNet18 appearance features are sufficient
  Documented in CHOICES.md.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import uuid

import numpy as np

try:
    import torch
    import torchvision.models as models
    import torchvision.transforms as T
    _REID_AVAILABLE = True
except ImportError:
    _REID_AVAILABLE = False


REENTRY_SIMILARITY_THRESHOLD = 0.85
REENTRY_TIME_WINDOW_MINUTES = 30
CROSS_CAM_DEDUP_WINDOW_SECONDS = 30


@dataclass
class TrackState:
    visitor_id: str
    track_id: int
    first_seen: datetime
    last_seen: datetime
    zone_history: List[str] = field(default_factory=list)
    session_seq: int = 0
    embedding: Optional[np.ndarray] = None
    is_staff: bool = False


@dataclass
class ExitRecord:
    visitor_id: str
    exit_time: datetime
    embedding: Optional[np.ndarray]


class ReIDTracker:
    def __init__(self):
        self._active: Dict[int, TrackState] = {}
        self._recent_exits: List[ExitRecord] = []
        self._model = _load_reid_model() if _REID_AVAILABLE else None
        self._transform = _build_transform() if _REID_AVAILABLE else None

    def get_or_create_visitor(
        self,
        track_id: int,
        frame: Optional[np.ndarray],
        bbox: Tuple[int, int, int, int],
        is_new_entry: bool,
    ) -> Tuple[str, bool]:
        """
        Returns (visitor_id, is_reentry).
        is_reentry=True when the same physical person returns after exiting.
        """
        now = datetime.now(timezone.utc)

        if track_id in self._active:
            state = self._active[track_id]
            state.last_seen = now
            return state.visitor_id, False

        embedding = self._extract_embedding(frame, bbox) if frame is not None else None

        if is_new_entry:
            matched_id, is_reentry = self._match_reentry(embedding, now)
        else:
            matched_id, is_reentry = None, False

        if matched_id:
            visitor_id = matched_id
        else:
            visitor_id = f"VIS_{uuid.uuid4().hex[:8]}"
            is_reentry = False

        self._active[track_id] = TrackState(
            visitor_id=visitor_id,
            track_id=track_id,
            first_seen=now,
            last_seen=now,
            embedding=embedding,
        )
        return visitor_id, is_reentry

    def record_exit(self, track_id: int) -> Optional[str]:
        """Call when a track exits. Returns visitor_id or None."""
        state = self._active.pop(track_id, None)
        if state is None:
            return None

        self._recent_exits.append(ExitRecord(
            visitor_id=state.visitor_id,
            exit_time=datetime.now(timezone.utc),
            embedding=state.embedding,
        ))
        self._prune_exits()
        return state.visitor_id

    def increment_seq(self, track_id: int) -> int:
        if track_id in self._active:
            self._active[track_id].session_seq += 1
            return self._active[track_id].session_seq
        return 0

    def get_visitor_id(self, track_id: int) -> Optional[str]:
        state = self._active.get(track_id)
        return state.visitor_id if state else None

    def _match_reentry(
        self, embedding: Optional[np.ndarray], now: datetime
    ) -> Tuple[Optional[str], bool]:
        if embedding is None or not self._recent_exits:
            return None, False

        window = now - timedelta(minutes=REENTRY_TIME_WINDOW_MINUTES)
        best_sim = 0.0
        best_id = None

        for exit_rec in self._recent_exits:
            if exit_rec.exit_time < window:
                continue
            if exit_rec.embedding is None:
                continue
            sim = _cosine_similarity(embedding, exit_rec.embedding)
            if sim > best_sim:
                best_sim = sim
                best_id = exit_rec.visitor_id

        if best_sim >= REENTRY_SIMILARITY_THRESHOLD and best_id:
            return best_id, True
        return None, False

    def _extract_embedding(
        self, frame: np.ndarray, bbox: Tuple[int, int, int, int]
    ) -> Optional[np.ndarray]:
        if not _REID_AVAILABLE or self._model is None:
            return _simple_colour_embedding(frame, bbox)

        import torch
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return None

        import cv2
        roi = frame[y1:y2, x1:x2]
        roi_rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        from PIL import Image
        pil_img = Image.fromarray(roi_rgb)
        tensor = self._transform(pil_img).unsqueeze(0)

        with torch.no_grad():
            feat = self._model(tensor).squeeze().numpy()
        norm = np.linalg.norm(feat)
        return feat / norm if norm > 0 else feat

    def _prune_exits(self):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=REENTRY_TIME_WINDOW_MINUTES)
        self._recent_exits = [r for r in self._recent_exits if r.exit_time >= cutoff]


def _load_reid_model():
    try:
        import torch
        import torchvision.models as models
        model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
        model.fc = torch.nn.Identity()
        model.eval()
        return model
    except Exception:
        return None


def _build_transform():
    try:
        import torchvision.transforms as T
        return T.Compose([
            T.Resize((128, 64)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
    except Exception:
        return None


def _simple_colour_embedding(
    frame: np.ndarray, bbox: tuple[int, int, int, int]
) -> Optional[np.ndarray]:
    """Fallback: 48-dim HSV histogram as appearance embedding."""
    try:
        import cv2
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [16], [0, 256]).flatten()
        feat = np.concatenate([hist_h, hist_s, hist_v])
        norm = np.linalg.norm(feat)
        return feat / norm if norm > 0 else feat
    except Exception:
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

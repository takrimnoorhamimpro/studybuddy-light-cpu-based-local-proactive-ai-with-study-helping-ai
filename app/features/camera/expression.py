"""Offline facial-expression classification using a compact ONNX FER+ CNN.

The model classifies the visible face crop into dataset-style expression labels.
These are uncertain visual estimates, not evidence of a person's actual feelings.
Frames are processed in memory only and are never saved or sent to the LLM.
"""
import os
import time
from collections import deque


# Output order used by the ONNX Model Zoo emotion-ferplus-8 model.
CLASS_NAMES = (
    "neutral", "happy", "surprised", "sad", "angry", "disgusted", "afraid", "contempt"
)


class CameraExpressionAnalyzer:
    MAX_SAMPLES = 9
    SUMMARY_MAX_AGE_SECONDS = 12
    MIN_CONFIDENCE = 0.38
    MIN_MARGIN = 0.08

    def __init__(self, model_path):
        self.model_path = os.path.abspath(model_path)
        self.session = None
        self.input_name = None
        self.last_summary = ""
        self.last_update = 0.0
        self.last_error = ""
        self.last_face_box = None
        self.last_probabilities = []
        self.last_face_detected = False
        self._samples = deque(maxlen=self.MAX_SAMPLES)
        if not os.path.isfile(self.model_path):
            self.last_error = "The offline FER+ ONNX model is missing. Run install_camera.bat once while online to download it."
            return
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self.session = ort.InferenceSession(
                self.model_path, sess_options=options, providers=["CPUExecutionProvider"]
            )
            inputs = self.session.get_inputs()
            if not inputs:
                raise RuntimeError("ONNX model has no input tensor")
            self.input_name = inputs[0].name
        except Exception as exc:
            self.session = None
            self.last_error = f"ONNX expression model initialization failed: {exc}"

    @property
    def available(self):
        return self.session is not None

    @staticmethod
    def _softmax(values):
        import numpy as np
        values = np.asarray(values, dtype=np.float64).reshape(-1)
        if not values.size:
            return []
        values = values - np.max(values)
        exps = np.exp(values)
        total = float(exps.sum())
        return (exps / total).tolist() if total else [1.0 / len(values)] * len(values)

    @classmethod
    def _format_probabilities(cls, probabilities):
        """Turn a probability vector into a cautious, readable short summary."""
        pairs = sorted(
            ((CLASS_NAMES[i] if i < len(CLASS_NAMES) else f"class_{i}", float(p))
             for i, p in enumerate(probabilities)), key=lambda item: item[1], reverse=True
        )
        if not pairs:
            return "No usable expression prediction was produced."
        label, confidence = pairs[0]
        second_confidence = pairs[1][1] if len(pairs) > 1 else 0.0
        top_three = ", ".join(f"{name} {score:.0%}" for name, score in pairs[:3])
        if confidence < cls.MIN_CONFIDENCE or confidence - second_confidence < cls.MIN_MARGIN:
            return (f"uncertain/mixed visible-expression estimate; leading model scores: {top_three}. "
                    "Do not infer the person's actual mood from these scores.")
        return (f"visible expression most resembles {label} in the FER+ training labels "
                f"(model score {confidence:.0%}); next scores: {top_three}. "
                "This is an uncertain facial-image classification, not a claim about actual feelings.")

    def process_bgr_frame(self, frame):
        """Classify a face in one OpenCV BGR frame; smooth recent predictions."""
        if not self.session:
            return ""
        try:
            import cv2
            import numpy as np

            if frame is None or getattr(frame, "size", 0) == 0:
                return self.get_recent_summary()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if not hasattr(self, "_face_detector"):
                # Prefer the project-bundled cascade. Some Windows Python/OpenCV
                # installs point cv2.data.haarcascades at a directory where the XML
                # file is missing, which otherwise breaks every camera inference.
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                bundled_cascade = os.path.join(project_root, "models", "haarcascade_frontalface_default.xml")
                candidates = [bundled_cascade]
                try:
                    candidates.append(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
                except Exception:
                    pass
                cascade_path = next((path for path in candidates if os.path.isfile(path)), "")
                if not cascade_path:
                    raise RuntimeError(
                        "Face detector XML is missing. Restore models/haarcascade_frontalface_default.xml "
                        "from the project package."
                    )
                self._face_detector = cv2.CascadeClassifier(cascade_path)
                if self._face_detector.empty():
                    raise RuntimeError(f"OpenCV could not load the face detector XML: {cascade_path}")
            faces = self._face_detector.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
            )
            now = time.time()
            if len(faces) == 0:
                self.last_face_box = None
                self.last_probabilities = []
                self.last_face_detected = False
                self._samples.clear()
                self.last_summary = "No face detected in the latest camera frame; no expression estimate is available."
                self.last_update = now
                return self.last_summary

            # Use the largest detected face and a small margin around it.
            x, y, w, h = max(faces, key=lambda box: int(box[2]) * int(box[3]))
            self.last_face_box = (int(x), int(y), int(w), int(h))
            self.last_face_detected = True
            margin_x, margin_y = int(w * 0.12), int(h * 0.12)
            x1, y1 = max(0, x - margin_x), max(0, y - margin_y)
            x2, y2 = min(gray.shape[1], x + w + margin_x), min(gray.shape[0], y + h + margin_y)
            face = gray[y1:y2, x1:x2]
            face = cv2.resize(face, (64, 64), interpolation=cv2.INTER_AREA)
            # FER+ reference preprocessing uses grayscale pixels scaled to [0, 1].
            tensor = face.astype(np.float32)[None, None, :, :]
            output = self.session.run(None, {self.input_name: tensor})[0]
            scores = self._softmax(output)
            if len(scores) < 7:
                raise RuntimeError(f"Expected at least 7 model classes, received {len(scores)}")
            self._samples.append(scores[:len(CLASS_NAMES)])
            mean_scores = np.mean(np.asarray(self._samples, dtype=np.float64), axis=0)
            self.last_probabilities = [float(v) for v in mean_scores.tolist()]
            self.last_summary = (
                f"Offline FER+ CNN prediction smoothed across {len(self._samples)} recent frames: "
                + self._format_probabilities(mean_scores.tolist())
            )
            self.last_update = now
            self.last_error = ""
            return self.last_summary
        except Exception as exc:
            self.last_error = f"Offline expression inference failed: {exc}"
            return ""

    def get_live_overlay(self):
        """Return the latest local detection result for drawing on the preview."""
        pairs = sorted(
            ((CLASS_NAMES[i] if i < len(CLASS_NAMES) else f"class_{i}", p)
             for i, p in enumerate(self.last_probabilities)), key=lambda item: item[1], reverse=True
        )
        return {"face_box": self.last_face_box, "face_detected": self.last_face_detected,
                "probabilities": pairs, "available": self.available}

    def get_recent_summary(self, max_age_seconds=SUMMARY_MAX_AGE_SECONDS):
        if self.last_summary and time.time() - self.last_update <= max_age_seconds:
            return self.last_summary
        return ""

    def close(self):
        # onnxruntime sessions are released when this reference is dropped.
        self.session = None
        self._samples.clear()

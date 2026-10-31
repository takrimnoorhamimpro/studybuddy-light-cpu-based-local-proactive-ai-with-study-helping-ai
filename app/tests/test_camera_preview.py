"""Hardware-free integration tests for the camera preview lifecycle."""
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

from features.camera import preview as camera_preview


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.after_callbacks = {}
        self.after_cancelled = []
        self.destroyed = False
        self.configurations = []
        self.bindings = {}
        self.width = 640
        self.height = 480

    def pack(self, *args, **kwargs): pass
    def title(self, *args, **kwargs): pass
    def geometry(self, *args, **kwargs): pass
    def minsize(self, *args, **kwargs): pass
    def transient(self, *args, **kwargs): pass
    def protocol(self, name, callback): self.protocol_callback = callback
    def bind(self, name, callback, **kwargs): self.bindings[name] = callback
    def after(self, ms, callback):
        self.after_callbacks["job"] = callback
        return "job"
    def after_cancel(self, job): self.after_cancelled.append(job)
    def winfo_exists(self): return not self.destroyed
    def winfo_width(self): return self.width
    def winfo_height(self): return self.height
    def configure(self, **kwargs): self.configurations.append(kwargs)
    def destroy(self): self.destroyed = True


class FakeCapture:
    def __init__(self, opened=True, read_result=None, read_error=None):
        self.opened = opened
        self.read_result = read_result or (True, FakeFrame())
        self.read_error = read_error
        self.released = False
        self.read_count = 0

    def isOpened(self): return self.opened
    def release(self): self.released = True
    def read(self):
        self.read_count += 1
        if self.read_error:
            raise self.read_error
        return self.read_result


class FakeFrame:
    shape = (480, 640, 3)


class CameraPreviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.capture = FakeCapture()
        self.cv2 = types.ModuleType("cv2")
        self.cv2.CAP_DSHOW = 700
        self.cv2.COLOR_BGR2RGB = 1
        self.cv2.VideoCapture = MagicMock(return_value=self.capture)
        self.cv2.cvtColor = MagicMock(side_effect=lambda frame, _code: frame)
        self.cv2.resize = MagicMock(side_effect=lambda frame, *_args: frame)
        self.pil = types.ModuleType("PIL")
        self.pil.Image = types.SimpleNamespace(fromarray=lambda frame: frame)
        self.pil.ImageTk = types.SimpleNamespace(PhotoImage=lambda **kwargs: object())
        self.window = FakeWidget()
        self.states = []

    def _run_with_fakes(self, settings):
        patches = [
            patch.dict(sys.modules, {"cv2": self.cv2, "PIL": self.pil}),
            patch.object(camera_preview.tk, "Toplevel", return_value=self.window),
            patch.object(camera_preview.ttk, "Label", side_effect=lambda *a, **k: FakeWidget()),
            patch.object(camera_preview.ttk, "Button", side_effect=lambda *a, **k: FakeWidget()),
            patch.object(camera_preview.messagebox, "askyesno", return_value=True),
            patch.object(camera_preview.messagebox, "showinfo"),
            patch.object(camera_preview.messagebox, "showerror"),
        ]
        stack = []
        try:
            for item in patches:
                stack.append(item)
                item.__enter__()
            return camera_preview.open_camera_preview(object(), settings, self.states.append)
        finally:
            for item in reversed(stack):
                item.__exit__(None, None, None)

    def test_disabled_camera_never_opens_device(self):
        with patch.object(camera_preview.messagebox, "showinfo"), patch.object(camera_preview.messagebox, "askyesno") as ask:
            result = camera_preview.open_camera_preview(object(), {"webcam_enabled": False})
        self.assertIsNone(result)
        ask.assert_not_called()

    def test_declined_consent_never_opens_device(self):
        with patch.object(camera_preview.messagebox, "askyesno", return_value=False), patch.dict(sys.modules, {"cv2": self.cv2}):
            result = camera_preview.open_camera_preview(object(), {"webcam_enabled": True})
        self.assertIsNone(result)
        self.cv2.VideoCapture.assert_not_called()

    def test_successful_preview_reads_frames_and_releases_on_close(self):
        win = self._run_with_fakes({"webcam_enabled": True, "camera_frame_interval_ms": 200})
        self.assertIs(win, self.window)
        self.assertEqual(self.states, [True])
        self.assertEqual(self.cv2.VideoCapture.call_args.args, (0, 700))
        callback = self.window.after_callbacks["job"]
        callback()
        self.assertEqual(self.capture.read_count, 1)
        self.window.protocol_callback()
        self.assertTrue(self.capture.released)
        self.assertEqual(self.states, [True, False])

    def test_camera_read_exception_stops_and_releases_device(self):
        self.capture.read_error = RuntimeError("simulated read failure")
        win = self._run_with_fakes({"webcam_enabled": True})
        win.after_callbacks["job"]()
        self.assertTrue(self.capture.released)
        self.assertEqual(self.states, [True, False])

    def test_unavailable_camera_is_released(self):
        self.capture.opened = False
        with patch.dict(sys.modules, {"cv2": self.cv2, "PIL": self.pil}), \
             patch.object(camera_preview.messagebox, "askyesno", return_value=True), \
             patch.object(camera_preview.messagebox, "showerror"):
            result = camera_preview.open_camera_preview(object(), {"webcam_enabled": True})
        self.assertIsNone(result)
        self.assertTrue(self.capture.released)


if __name__ == "__main__":
    unittest.main()

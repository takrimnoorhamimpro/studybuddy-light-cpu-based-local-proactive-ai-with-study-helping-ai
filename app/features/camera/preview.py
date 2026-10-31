"""Explicit, preview-only webcam window with defensive cleanup.

The module never opens a camera on import. Frames are held only in memory for
rendering and are never recorded or persisted.
"""
import tkinter as tk
import time
from tkinter import ttk, messagebox


def open_camera_preview(parent, settings, on_state_change=None, consent_already_given=False, on_frame=None, on_overlay=None):
    if not settings.get("webcam_enabled", False):
        messagebox.showinfo("Camera disabled", "Enable webcam access in Settings first.", parent=parent)
        return None
    if not consent_already_given and not messagebox.askyesno(
        "Allow camera access?",
        "Study Buddy will open the webcam for a live preview only.\n\n"
        "No images or video are saved, and the camera will stop when this window closes.\n\nOpen camera now?",
        parent=parent,
    ):
        return None

    try:
        import cv2
        from PIL import Image, ImageTk
    except Exception as exc:
        messagebox.showerror(
            "Camera dependencies missing",
            "Install the optional camera dependencies with:\n\n"
            "py -3.12 -m pip install opencv-python pillow\n\nDetails: " + str(exc),
            parent=parent,
        )
        return None

    device_index = max(0, int(settings.get("camera_device_index", 0)))
    try:
        backend = getattr(cv2, "CAP_DSHOW", 0)
        capture = cv2.VideoCapture(device_index, backend)
        if not capture.isOpened():
            capture.release()
            messagebox.showerror(
                "Camera unavailable",
                "Could not open the webcam. Check Windows camera permission and close other apps using it.",
                parent=parent,
            )
            return None
    except Exception as exc:
        try:
            capture.release()
        except Exception:
            pass
        messagebox.showerror("Camera error", "Could not initialize the webcam:\n\n" + str(exc), parent=parent)
        return None

    try:
        win = tk.Toplevel(parent)
        win.title("Study Buddy — Camera Preview")
        win.geometry("700x560")
        win.minsize(420, 360)
        win.transient(parent)
        indicator = ttk.Label(win, text="● CAMERA ACTIVE  |  LIVE PREVIEW ONLY — NOT RECORDING", foreground="#b91c1c")
        indicator.pack(fill="x", padx=12, pady=(12, 4))
        preview = ttk.Label(win, text="Starting camera…", anchor="center")
        preview.pack(fill="both", expand=True, padx=12, pady=8)
        footer = ttk.Label(win, text="Close this window to stop camera access.")
        footer.pack(fill="x", padx=12, pady=(0, 6))
        close_btn = ttk.Button(win, text="Stop camera", command=win.destroy)
        close_btn.pack(anchor="e", padx=12, pady=(0, 12))
    except Exception as exc:
        capture.release()
        messagebox.showerror("Camera window error", "Could not create the preview window:\n\n" + str(exc), parent=parent)
        return None

    state = {"closed": False, "image": None, "after_id": None, "last_analysis_at": 0.0}
    interval_ms = max(100, min(1000, int(settings.get("camera_frame_interval_ms", 200))))

    def close_camera():
        if state["closed"]:
            return
        state["closed"] = True
        if state["after_id"] is not None:
            try:
                win.after_cancel(state["after_id"])
            except Exception:
                pass
            state["after_id"] = None
        try:
            capture.release()
        finally:
            if on_state_change:
                on_state_change(False)

    def update_frame():
        if state["closed"]:
            return
        try:
            if not win.winfo_exists():
                close_camera()
                return
            ok, frame = capture.read()
            if ok:
                if on_frame and (time.monotonic() - state["last_analysis_at"] >= 0.65):
                    state["last_analysis_at"] = time.monotonic()
                    try:
                        on_frame(frame)
                    except Exception:
                        pass
                # Draw only local overlays; the raw frame is never recorded or sent to an LLM.
                overlay = None
                if on_overlay:
                    try:
                        overlay = on_overlay()
                    except Exception:
                        overlay = None
                if overlay:
                    if overlay.get("face_box"):
                        x, y, w, h = overlay["face_box"]
                        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 2)
                        label = "FACE DETECTED"
                        probs = overlay.get("probabilities") or []
                        if probs:
                            label = f"{probs[0][0].upper()} {probs[0][1]:.0%}"
                        cv2.rectangle(frame, (x, max(0, y - 28)), (x + min(w, 220), y), (0, 0, 255), -1)
                        cv2.putText(frame, label, (x + 5, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
                    # Compact confidence panel in the preview's top-left corner.
                    probs = overlay.get("probabilities") or []
                    if overlay.get("available"):
                        panel_w, line_h = 205, 22
                        panel_h = 32 + min(8, len(probs)) * line_h
                        cv2.rectangle(frame, (8, 8), (8 + panel_w, 8 + panel_h), (24, 24, 24), -1)
                        cv2.putText(frame, "LIVE EXPRESSION SCORES", (16, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
                        for idx, (name, score) in enumerate(probs[:8]):
                            cv2.putText(frame, f"{name.title():10s} {score:5.1%}", (16, 51 + idx * line_h), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (230, 230, 230), 1, cv2.LINE_AA)
                    elif not overlay.get("face_detected"):
                        cv2.putText(frame, "No face detected", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                height, width = frame.shape[:2]
                max_w, max_h = max(320, preview.winfo_width()), max(240, preview.winfo_height())
                scale = min(max_w / max(1, width), max_h / max(1, height), 1.0)
                if scale < 1.0:
                    frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
                image = Image.fromarray(frame)
                state["image"] = ImageTk.PhotoImage(image=image)
                preview.configure(image=state["image"], text="")
            else:
                preview.configure(text="No camera frame received. Check the camera connection.", image="")
            state["after_id"] = win.after(interval_ms, update_frame)
        except Exception as exc:
            try:
                preview.configure(text="Camera stopped after an error: " + str(exc), image="")
            except Exception:
                pass
            close_camera()

    def stop_and_destroy():
        close_camera()
        try:
            win.destroy()
        except Exception:
            pass

    win._studybuddy_stop = stop_and_destroy
    win.protocol("WM_DELETE_WINDOW", stop_and_destroy)
    win.bind("<Destroy>", lambda event: close_camera() if event.widget is win else None, add="+")
    if on_state_change:
        on_state_change(True)
    state["after_id"] = win.after(0, update_frame)
    return win

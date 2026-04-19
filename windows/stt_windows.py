"""
STT - Speech to Text (Windows)
Double-tap Ctrl to start recording, single tap Ctrl to stop.
Live preview at bottom of screen. Final text pasted at cursor.
Powered by Groq Whisper API.
"""

import io
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import wave

import numpy as np
import sounddevice as sd
from pynput import keyboard

# ── Settings ───────────────────────────────────────────────────────────
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".config", "stt", "settings.json")
GROQ_MODEL = "whisper-large-v3-turbo"
SAMPLE_RATE = 16000
CHANNELS = 1
DOUBLE_TAP_WINDOW = 0.4
LIVE_INTERVAL = 2.0
PREVIEW_LINGER = 3.0


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f)


def get_api_key():
    settings = load_settings()
    key = settings.get("groq_api_key", "")
    if key:
        return key
    return os.environ.get("GROQ_API_KEY", "")


# ── Groq transcription ────────────────────────────────────────────────
_groq_client = None


def get_groq():
    global _groq_client
    from groq import Groq
    key = get_api_key()
    _groq_client = Groq(api_key=key)
    return _groq_client


def transcribe_audio(audio_np):
    client = get_groq()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        audio_int16 = (audio_np * 32767).astype(np.int16)
        wf.writeframes(audio_int16.tobytes())
    buf.seek(0)
    transcription = client.audio.transcriptions.create(
        file=("audio.wav", buf.read()),
        model=GROQ_MODEL,
        response_format="text",
    )
    return transcription.strip()


def paste_text(text):
    """Copy to clipboard and simulate Ctrl+V with retry."""
    if not text:
        return
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.2)
    from pynput.keyboard import Controller, Key
    kb = Controller()
    kb.press(Key.ctrl)
    kb.press("v")
    time.sleep(0.03)
    kb.release("v")
    kb.release(Key.ctrl)
    time.sleep(0.1)


# ── Overlay Window (Tkinter) ──────────────────────────────────────────

class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("STT")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)

        # Click-through on Windows
        try:
            self.root.attributes("-transparentcolor", "#010101")
        except Exception:
            pass

        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.bar_h = 44
        self.root.geometry(f"{self.screen_w}x{self.bar_h}+0+{self.screen_h - self.bar_h}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.screen_w,
            height=self.bar_h,
            bg="#010101",
            highlightthickness=0,
        )
        self.canvas.pack()

        # State
        self.recording = False
        self.frames = []
        self.stream = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_ctrl_release = 0.0
        self.ctrl_was_solo = True
        self.ctrl_is_down = False
        self.live_text = ""
        self.display_text = ""
        self.state = "idle"
        self.audio_level = 0.0
        self.smoothed_level = 0.0
        self.phase = 0.0
        self.text_opacity = 1.0
        self.target_text_opacity = 1.0
        self.visible = False
        self.hide_timer = None
        self.live_thread = None
        self.model_ready = False

        # API key check
        if not get_api_key():
            self.show_api_key_dialog()
        else:
            self.model_ready = True
            # Show ready notification
            self.root.after(500, self._show_ready)

        # Start keyboard listener
        threading.Thread(target=self._key_listener, daemon=True).start()

        # Animation loop
        self._animate()

        self.root.mainloop()

    def _show_ready(self):
        """Flash a brief 'STT Ready' notification."""
        self.state = "done"
        self.display_text = "STT Ready - Double-tap Ctrl to dictate"
        self.text_opacity = 1.0
        self.root.attributes("-alpha", 0.92)
        self.visible = True
        self.hide_timer = self.root.after(2000, self.hide)

    def show_api_key_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("STT - Setup")
        dialog.geometry("450x220")
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#1a1a1a")
        dialog.resizable(False, False)

        x = (self.screen_w - 450) // 2
        y = (self.screen_h - 220) // 2
        dialog.geometry(f"450x220+{x}+{y}")

        tk.Label(
            dialog, text="Welcome to STT",
            font=("Segoe UI", 16, "bold"), fg="white", bg="#1a1a1a",
        ).pack(pady=(15, 5))

        tk.Label(
            dialog,
            text="Enter your Groq API key (free at console.groq.com/keys)\nDouble-tap Ctrl to record, single tap Ctrl to stop.",
            font=("Segoe UI", 10), fg="#aaaaaa", bg="#1a1a1a",
        ).pack(pady=(0, 10))

        entry = tk.Entry(
            dialog, font=("Segoe UI", 11), width=40,
            bg="#2a2a2a", fg="white", insertbackground="white",
        )
        entry.pack(pady=5, padx=20)
        entry.focus()

        def save_key():
            key = entry.get().strip()
            if key:
                settings = load_settings()
                settings["groq_api_key"] = key
                save_settings(settings)
                self.model_ready = True
                dialog.destroy()
                self.root.after(200, self._show_ready)

        tk.Button(
            dialog, text="Save & Start", font=("Segoe UI", 11),
            bg="#2d8c4e", fg="white", command=save_key, width=15,
        ).pack(pady=10)

        dialog.grab_set()
        dialog.wait_window()

    def _animate(self):
        if self.state != "idle":
            self.phase += 0.04
            self.smoothed_level += (self.audio_level - self.smoothed_level) * 0.2
            self.text_opacity += (self.target_text_opacity - self.text_opacity) * 0.08
            self._draw()
        self.root.after(16, self._animate)

    def _draw(self):
        self.canvas.delete("all")
        w = self.screen_w
        h = self.bar_h

        level = self.smoothed_level
        breath = 0.7 + 0.3 * (0.5 + 0.5 * math.sin(self.phase))
        intensity = min(max(breath, breath + level * 0.5), 1.0)

        # Background gradient
        steps = 12
        for i in range(steps):
            frac = i / float(steps)
            y1 = int(frac * h)
            y2 = int((frac + 1.0 / steps) * h) + 1
            v = int((0.035 + frac * 0.03) * 255)
            color = f"#{v:02x}{v:02x}{v + 2:02x}"
            self.canvas.create_rectangle(0, h - y2, w, h - y1, fill=color, outline="")

        if self.state == "recording":
            # Green light at bottom
            gi = int(220 * intensity)
            self.canvas.create_rectangle(0, h - 2, w, h, fill=f"#14{gi:02x}60", outline="")
            # Bright core
            gc = int(255 * intensity)
            self.canvas.create_rectangle(0, h - 1, w, h, fill=f"#40{gc:02x}80", outline="")

            # Glow layers
            glow_h = 28
            layers = 10
            for i in range(layers):
                frac = i / float(layers)
                falloff = math.exp(-frac * 3.5)
                alpha_val = falloff * 0.15 * intensity * (1.0 + level * 2.0)
                if alpha_val < 0.01:
                    break
                y = h - 2 - int(frac * glow_h)
                g = int((0.88 - frac * 0.2) * 255 * alpha_val * 3)
                b = int((0.42 + frac * 0.2) * 255 * alpha_val * 3)
                g = min(g, 255)
                b = min(b, 255)
                color = f"#{int(15 * alpha_val * 3):02x}{g:02x}{b:02x}"
                try:
                    self.canvas.create_rectangle(
                        0, y - int(glow_h / layers), w, y + 1,
                        fill=color, outline=""
                    )
                except Exception:
                    pass

            # Audio bloom center
            if level > 0.03:
                bloom_w = int(w * (0.25 + level * 0.5))
                bloom_x = (w - bloom_w) // 2
                for i in range(6):
                    frac = i / 6.0
                    falloff = math.exp(-frac * 2.8)
                    a = falloff * level * 0.3
                    g = int(min(240 * a * 4, 255))
                    bv = int(min(130 * a * 4, 255))
                    spread = 1.0 + frac * 0.5
                    bw = int(bloom_w * spread)
                    bx = (w - bw) // 2
                    y = h - 2 - int(frac * glow_h * 2)
                    try:
                        self.canvas.create_rectangle(
                            bx, y - 3, bx + bw, y + 1,
                            fill=f"#30{g:02x}{bv:02x}", outline=""
                        )
                    except Exception:
                        pass

        elif self.state == "done":
            self.canvas.create_rectangle(0, h - 2, w, h, fill="#30ff70", outline="")
            for i in range(6):
                frac = i / 6.0
                falloff = math.exp(-frac * 3.0)
                v = int(falloff * 30)
                g = int(falloff * 200)
                y = h - 2 - int(frac * 16)
                self.canvas.create_rectangle(
                    0, y - 2, w, y + 1,
                    fill=f"#{v:02x}{g:02x}{v + 20:02x}", outline=""
                )

        # Text
        opacity = max(0, min(self.text_opacity, 1.0))
        if self.display_text:
            if self.state == "recording":
                gray = int(220 * opacity)
                color = f"#{gray:02x}{gray:02x}{gray:02x}"
                txt = self.display_text + "  ..."
            else:
                gray = int(255 * opacity)
                color = f"#{gray:02x}{gray:02x}{gray:02x}"
                txt = self.display_text + "  \u2713"
            self.canvas.create_text(
                w // 2, h // 2 - 2,
                text=txt, fill=color,
                font=("Segoe UI", 11), anchor="center", width=w - 56,
            )
        elif self.state == "recording":
            pulse = 0.3 + 0.15 * math.sin(self.phase * 1.5)
            gray = int(120 * pulse)
            self.canvas.create_text(
                w // 2, h // 2 - 2,
                text="Listening...", fill=f"#{gray:02x}{gray:02x}{gray:02x}",
                font=("Segoe UI", 10), anchor="center",
            )

    def show(self):
        self.visible = True
        self.state = "recording"
        self.display_text = ""
        self.root.attributes("-alpha", 0.92)

    def hide(self):
        self.visible = False
        self.state = "idle"
        self.root.attributes("-alpha", 0.0)
        self.hide_timer = None

    def show_done(self, text):
        self.state = "done"
        self.display_text = text
        self.text_opacity = 0.0
        self.target_text_opacity = 1.0
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
        self.hide_timer = self.root.after(int(PREVIEW_LINGER * 1000), self.hide)

    def _set_text(self, text):
        if text != self.display_text:
            self.text_opacity = 0.0
            self.target_text_opacity = 1.0
            self.display_text = text

    def _toggle(self):
        if not self.model_ready:
            return
        with self.lock:
            if not self.recording:
                self._start_recording()
            else:
                self._stop_recording()

    def _start_recording(self):
        self.frames = []
        self.stop_event.clear()
        self.recording = True
        self.live_text = ""

        def audio_cb(indata, frame_count, time_info, status):
            if not self.stop_event.is_set():
                self.frames.append(indata.copy())
                rms = float(np.sqrt(np.mean(indata ** 2)))
                self.audio_level = min(rms * 18.0, 1.0)

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", callback=audio_cb, blocksize=1024,
        )
        self.stream.start()
        self.root.after(0, self.show)

        self.live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self.live_thread.start()

    def _live_loop(self):
        while not self.stop_event.is_set():
            time.sleep(LIVE_INTERVAL)
            if self.stop_event.is_set():
                break
            if not self.frames:
                continue
            audio = np.concatenate(list(self.frames), axis=0).flatten()
            if len(audio) / SAMPLE_RATE < 0.5:
                continue
            try:
                text = transcribe_audio(audio)
                if text:
                    self.live_text = text
                    self.root.after(0, lambda t=text: self._set_text(t))
            except Exception as e:
                print(f"Live error: {e}")

    def _stop_recording(self):
        self.recording = False
        self.stop_event.set()
        self.stream.stop()
        self.stream.close()

        if self.live_thread:
            self.live_thread.join(timeout=10)
            self.live_thread = None

        if not self.frames:
            self.root.after(0, self.hide)
            return

        audio = np.concatenate(self.frames, axis=0).flatten()
        if len(audio) / SAMPLE_RATE < 0.3:
            self.root.after(0, self.hide)
            return

        threading.Thread(target=self._final_transcribe, args=(audio,), daemon=True).start()

    def _final_transcribe(self, audio):
        try:
            text = transcribe_audio(audio)
            if text:
                self.root.after(0, lambda: self.show_done(text))
                time.sleep(0.15)
                paste_text(text)
            else:
                self.root.after(0, self.hide)
        except Exception as e:
            print(f"Error: {e}")
            self.root.after(0, self.hide)

    def _key_listener(self):
        """Double-tap Ctrl to start, single tap Ctrl to stop."""
        def on_press(key):
            if key not in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                self.ctrl_was_solo = False

        def on_release(key):
            if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                if not self.ctrl_was_solo:
                    self.ctrl_was_solo = True
                    self.last_ctrl_release = 0.0
                    return

                # Single tap stops recording
                if self.recording:
                    threading.Thread(target=self._toggle, daemon=True).start()
                    self.last_ctrl_release = 0.0
                else:
                    # Double tap starts recording
                    now = time.time()
                    if now - self.last_ctrl_release < DOUBLE_TAP_WINDOW:
                        self.last_ctrl_release = 0.0
                        threading.Thread(target=self._toggle, daemon=True).start()
                    else:
                        self.last_ctrl_release = now

                self.ctrl_was_solo = True

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()


def main():
    # Kill existing instances
    if sys.platform == "win32":
        try:
            import psutil
            my_pid = os.getpid()
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if proc.info["pid"] != my_pid and proc.info["cmdline"]:
                        if "stt_windows" in " ".join(proc.info["cmdline"]):
                            proc.kill()
                except Exception:
                    pass
        except ImportError:
            pass

    app = OverlayApp()


if __name__ == "__main__":
    main()

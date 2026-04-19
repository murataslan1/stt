#!/opt/homebrew/opt/python@3.14/bin/python3.14
"""
STT - Local Speech-to-Text (MLX Whisper)
Double-tap ⌘ to record, double-tap ⌘ to stop.
Live preview at bottom of screen shows words as you speak.
Final text is pasted at your cursor.
"""

import math
import os
import subprocess
import threading
import time

import numpy as np
import objc
import json
import Quartz
import sounddevice as sd
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAnimationContext,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBezierPath,
    NSCenterTextAlignment,
    NSColor,
    NSEvent,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSObject,
    NSScreen,
    NSShadow,
    NSStatusBar,
    NSTextField,
    NSTimer,
    NSVariableStatusItemLength,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)

# ── Settings persistence ───────────────────────────────────────────────
SETTINGS_PATH = os.path.expanduser("~/.config/stt/settings.json")


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
    """Get API key from settings file, then env, then empty."""
    settings = load_settings()
    key = settings.get("groq_api_key", "")
    if key:
        return key
    return os.environ.get("GROQ_API_KEY", "")

# ── Config ──────────────────────────────────────────────────────────────
MODEL = "mlx-community/whisper-large-v3-turbo"
GROQ_MODEL = "whisper-large-v3-turbo"
GROQ_API_KEY = get_api_key()
USE_GROQ = bool(GROQ_API_KEY)  # Auto: Groq if key exists, else local
SAMPLE_RATE = 16000
CHANNELS = 1
DOUBLE_TAP_WINDOW = 0.4
FPS = 60
CMD_FLAG = 1 << 20
LIVE_INTERVAL = 2.0  # seconds between live transcription updates
PREVIEW_LINGER = 3.0  # how long the final text stays visible
# ────────────────────────────────────────────────────────────────────────

_whisper = None
_mlx_lock = threading.Lock()
_groq_client = None


def get_groq():
    global _groq_client, GROQ_API_KEY
    GROQ_API_KEY = get_api_key()
    if _groq_client is None or True:  # Always refresh in case key changed
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def get_whisper():
    global _whisper
    if _whisper is None:
        import mlx_whisper
        _whisper = mlx_whisper
    return _whisper


def transcribe_audio(audio_np):
    """Transcribe audio - tries Groq first, falls back to local."""
    if USE_GROQ:
        try:
            return _transcribe_groq(audio_np)
        except Exception as e:
            print(f"  Groq failed ({e}), falling back to local...")
            with _mlx_lock:
                return _transcribe_local(audio_np)
    else:
        with _mlx_lock:
            return _transcribe_local(audio_np)


def _transcribe_groq(audio_np):
    """Transcribe via Groq API - very fast, no local resources."""
    import io
    import wave

    client = get_groq()

    # Convert numpy audio to WAV bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
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


def _transcribe_local(audio_np):
    """Transcribe via local MLX Whisper."""
    whisper = get_whisper()
    result = whisper.transcribe(
        audio_np,
        path_or_hf_repo=MODEL,
        fp16=False,
        verbose=False,
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        logprob_threshold=-1.0,
        no_speech_threshold=0.6,
    )
    segments = result.get("segments", [])
    texts = []
    seen = set()
    for seg in segments:
        text = seg["text"].strip()
        if not text or text in seen:
            continue
        if seg.get("compression_ratio", 0) > 2.4:
            continue
        if seg.get("no_speech_prob", 0) > 0.6:
            continue
        seen.add(text)
        texts.append(text)
    return " ".join(texts).strip()


_pasting = False  # flag to ignore our own paste events


def get_frontmost_app():
    """Get the bundle ID of the frontmost app."""
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app:
        return app.bundleIdentifier()
    return None


def paste_at_cursor(text, target_bundle_id=None):
    """Copy text to clipboard and auto-paste at cursor in the target app."""
    global _pasting
    if not text:
        return

    # Step 1: Copy to clipboard via NSPasteboard (more reliable than pbcopy for some apps)
    from AppKit import NSPasteboard, NSStringPboardType
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSStringPboardType)
    time.sleep(0.05)

    _pasting = True

    # Step 2: Activate target app
    if target_bundle_id:
        from AppKit import NSWorkspace, NSRunningApplication
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(target_bundle_id)
        if apps:
            apps[0].activateWithOptions_(3)  # NSApplicationActivateIgnoringOtherApps
            time.sleep(0.15)

    # Step 3: Paste via CGEvent - just Cmd+V with flags on the key event
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    cmd_flag = Quartz.kCGEventFlagMaskCommand

    v_down = Quartz.CGEventCreateKeyboardEvent(src, 9, True)
    Quartz.CGEventSetFlags(v_down, cmd_flag)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, v_down)

    time.sleep(0.05)

    v_up = Quartz.CGEventCreateKeyboardEvent(src, 9, False)
    Quartz.CGEventSetFlags(v_up, cmd_flag)
    Quartz.CGEventPost(Quartz.kCGAnnotatedSessionEventTap, v_up)

    time.sleep(0.1)
    _pasting = False
    print("  Auto-pasted")


# ── Visual Config ──────────────────────────────────────────────────────

TOP_CORNER = 14
MIN_BAR_HEIGHT = 44
MAX_BAR_HEIGHT = 240
TEXT_PADDING_H = 28
TEXT_PADDING_V = 14
TEXT_FONT_SIZE = 13.5
GLOW_HEIGHT = 28  # taller glow for more realistic light spread


class BottomBarView(NSView):
    """Full-width bottom bar with realistic light, text fade-in, rounded top corners."""

    def initWithFrame_(self, frame):
        self = objc.super(BottomBarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._phase = 0.0
        self._audio_level = 0.0
        self._smoothed_level = 0.0
        self._text = ""
        self._prev_text = ""
        self._text_opacity = 1.0  # for fade-in
        self._target_text_opacity = 1.0
        self._state = "idle"
        self._desired_height = MIN_BAR_HEIGHT
        return self

    @objc.python_method
    def set_audio_level(self, level):
        self._audio_level = min(level, 1.0)

    @objc.python_method
    def set_text(self, text):
        if text != self._text:
            self._prev_text = self._text
            self._text = text
            if text and text != self._prev_text:
                self._text_opacity = 0.0  # start fade-in
                self._target_text_opacity = 1.0
            self._recalc_height()
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_state(self, state):
        self._state = state
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _recalc_height(self):
        if not self._text:
            self._desired_height = MIN_BAR_HEIGHT
            return
        w = self.bounds().size.width
        if w < 1:
            return
        paragraph = NSMutableParagraphStyle.alloc().init()
        paragraph.setAlignment_(NSCenterTextAlignment)
        paragraph.setLineBreakMode_(0)
        paragraph.setLineSpacing_(4.0)
        attrs = {
            "NSFont": NSFont.systemFontOfSize_weight_(TEXT_FONT_SIZE, 0.2),
            "NSParagraphStyle": paragraph,
        }
        ns_str = NSAttributedString.alloc().initWithString_attributes_(
            self._text, attrs
        )
        text_w = w - TEXT_PADDING_H * 2
        bounding = ns_str.boundingRectWithSize_options_(
            NSMakeSize(text_w, 10000), 1 << 0 | 1 << 1
        )
        text_h = bounding.size.height
        total = text_h + TEXT_PADDING_V * 2 + 10
        self._desired_height = min(max(total, MIN_BAR_HEIGHT), MAX_BAR_HEIGHT)

    @objc.python_method
    def get_desired_height(self):
        return self._desired_height

    @objc.python_method
    def tick(self):
        self._phase += 0.04
        self._smoothed_level += (self._audio_level - self._smoothed_level) * 0.2
        # Smooth text fade-in
        self._text_opacity += (self._target_text_opacity - self._text_opacity) * 0.08
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        ctx = NSGraphicsContext.currentContext()
        if not ctx:
            return
        w = self.bounds().size.width
        h = self.bounds().size.height

        if self._state == "idle":
            return

        level = self._smoothed_level
        breath = 0.7 + 0.3 * (0.5 + 0.5 * math.sin(self._phase))
        intensity = min(max(breath, breath + level * 0.5), 1.0)
        opacity = self._text_opacity

        # ── Shape: full width, rounded top corners, flat bottom ──
        shape = NSBezierPath.alloc().init()
        shape.moveToPoint_((0, 0))
        shape.lineToPoint_((0, h - TOP_CORNER))
        shape.curveToPoint_controlPoint1_controlPoint2_(
            (TOP_CORNER, h), (0, h), (TOP_CORNER, h)
        )
        shape.lineToPoint_((w - TOP_CORNER, h))
        shape.curveToPoint_controlPoint1_controlPoint2_(
            (w, h - TOP_CORNER), (w, h), (w, h - TOP_CORNER)
        )
        shape.lineToPoint_((w, 0))
        shape.closePath()

        ctx.saveGraphicsState()
        shape.addClip()

        # ── Background: smooth gradient ──
        steps = 32
        for i in range(steps):
            frac = i / float(steps)
            y = frac * h
            step_h = h / float(steps) + 1
            r = 0.035 + frac * 0.03
            g = 0.035 + frac * 0.025
            b = 0.04 + frac * 0.03
            NSColor.colorWithRed_green_blue_alpha_(r, g, b, 0.94).setFill()
            NSBezierPath.fillRect_(NSMakeRect(0, y, w, step_h))

        # ── Light source ──
        if self._state == "recording":
            # Core light: use wide flat ovals for smooth, pixel-free glow
            # Hot white-green core line
            core_oval = NSMakeRect(-w * 0.1, -4, w * 1.2, 6)
            NSColor.colorWithRed_green_blue_alpha_(
                0.4 * intensity, 1.0 * intensity, 0.55 * intensity, 0.9
            ).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(core_oval).fill()

            # Bright green band
            band_oval = NSMakeRect(-w * 0.05, -3, w * 1.1, 8)
            NSColor.colorWithRed_green_blue_alpha_(
                0.08, 0.85 * intensity, 0.38 * intensity, 0.7
            ).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(band_oval).fill()

            # ── Main glow: stacked ovals getting larger and more transparent ──
            glow_layers = 30
            for i in range(glow_layers):
                frac = i / float(glow_layers)
                # Smooth exponential falloff
                falloff = math.exp(-frac * 3.5)
                alpha = falloff * 0.09 * intensity * (1.0 + level * 2.0)
                if alpha < 0.002:
                    break

                oval_h = 4 + frac * GLOW_HEIGHT * 2.2
                oval_w = w * (1.1 - frac * 0.15)
                oval_x = (w - oval_w) / 2.0
                oval_y = -oval_h * 0.35

                # Color temperature shift
                r = 0.12 * (1.0 - frac * 0.4)
                g = 0.88 * (1.0 - frac * 0.2)
                b = 0.42 + frac * 0.2
                NSColor.colorWithRed_green_blue_alpha_(r, g, b, alpha).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(oval_x, oval_y, oval_w, oval_h)
                ).fill()

            # ── Audio bloom: radial oval from center ──
            if level > 0.03:
                bloom_layers = 14
                for i in range(bloom_layers):
                    frac = i / float(bloom_layers)
                    falloff = math.exp(-frac * 2.8)
                    alpha = falloff * level * 0.2 * intensity
                    if alpha < 0.002:
                        break

                    bw = w * (0.2 + level * 0.45) * (1.0 + frac * 0.6)
                    bh = 6 + frac * GLOW_HEIGHT * 2.5
                    bx = (w - bw) / 2.0
                    by = -bh * 0.25
                    NSColor.colorWithRed_green_blue_alpha_(
                        0.2, 0.95, 0.5, alpha
                    ).setFill()
                    NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(bx, by, bw, bh)
                    ).fill()

            # ── Ambient ceiling reflection ──
            ambient = 0.012 * intensity * (1.0 + level * 1.5)
            ceil_oval = NSMakeRect(-w * 0.1, h * 0.5, w * 1.2, h * 0.8)
            NSColor.colorWithRed_green_blue_alpha_(0.1, 0.5, 0.3, ambient).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(ceil_oval).fill()

        elif self._state == "done":
            # Satisfying green flash - ovals
            core = NSMakeRect(-w * 0.1, -4, w * 1.2, 7)
            NSColor.colorWithRed_green_blue_alpha_(0.3, 1.0, 0.55, 0.9).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(core).fill()

            for i in range(12):
                frac = i / 12.0
                falloff = math.exp(-frac * 3.0)
                alpha = falloff * 0.06
                oh = 4 + frac * 20
                NSColor.colorWithRed_green_blue_alpha_(0.12, 0.85, 0.42, alpha).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(-w * 0.05, -oh * 0.3, w * 1.1, oh)
                ).fill()

        # ── Top edge highlight ──
        NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.03).setFill()
        NSBezierPath.fillRect_(NSMakeRect(TOP_CORNER, h - 0.5, w - TOP_CORNER * 2, 0.5))

        # ── Text with fade-in ──
        text_y = 8
        if self._text:
            paragraph = NSMutableParagraphStyle.alloc().init()
            paragraph.setAlignment_(NSCenterTextAlignment)
            paragraph.setLineBreakMode_(0)
            paragraph.setLineSpacing_(4.0)

            if self._state == "recording":
                base_alpha = 0.88 * opacity
                text_color = NSColor.colorWithRed_green_blue_alpha_(
                    0.88, 0.88, 0.88, base_alpha
                )
                display_text = self._text + "  ..."
            else:
                base_alpha = 0.95 * opacity
                text_color = NSColor.colorWithRed_green_blue_alpha_(
                    1.0, 1.0, 1.0, base_alpha
                )
                display_text = self._text + "  \u2713"

            attrs = {
                "NSFont": NSFont.systemFontOfSize_weight_(TEXT_FONT_SIZE, 0.2),
                "NSColor": text_color,
                "NSParagraphStyle": paragraph,
            }
            ns_str = NSAttributedString.alloc().initWithString_attributes_(
                display_text, attrs
            )
            text_rect = NSMakeRect(
                TEXT_PADDING_H, text_y,
                w - TEXT_PADDING_H * 2, h - text_y - TEXT_PADDING_V
            )
            ns_str.drawInRect_(text_rect)

        elif self._state == "recording":
            listen_alpha = 0.3 + 0.15 * math.sin(self._phase * 1.5)
            paragraph = NSMutableParagraphStyle.alloc().init()
            paragraph.setAlignment_(NSCenterTextAlignment)
            attrs = {
                "NSFont": NSFont.systemFontOfSize_weight_(12.0, 0.3),
                "NSColor": NSColor.colorWithRed_green_blue_alpha_(
                    0.45, 0.45, 0.45, listen_alpha
                ),
                "NSParagraphStyle": paragraph,
            }
            ns_str = NSAttributedString.alloc().initWithString_attributes_(
                "Listening...", attrs
            )
            text_rect = NSMakeRect(20, text_y, w - 40, 18)
            ns_str.drawInRect_(text_rect)

        ctx.restoreGraphicsState()


# ── App Delegate ────────────────────────────────────────────────────────

class AppDelegate(NSObject):

    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self._recording = False
        self._frames = []
        self._stream = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_cmd_release = 0.0
        self._cmd_was_solo = True
        self._cmd_is_down = False
        self._anim_timer = None
        self._bar_window = None
        self._bar_view = None
        self._status_item = None
        self._idle_image = None
        self._rec_image = None
        self._event_monitor = None
        self._hide_timer = None
        self._live_text = ""
        self._live_thread = None
        self._model_ready = False
        self._target_app = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        self._idle_image = self._make_dot(False)
        self._rec_image = self._make_dot(True)

        sb = NSStatusBar.systemStatusBar()
        self._status_item = sb.statusItemWithLength_(NSVariableStatusItemLength)
        # Use SF Symbol microphone icon if available, otherwise text
        mic_img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("mic.fill", "STT")
        if mic_img:
            mic_img.setTemplate_(True)
            self._idle_image = mic_img
            self._status_item.button().setImage_(mic_img)
        else:
            self._status_item.button().setTitle_("STT")
        self._status_item.button().setToolTip_("STT - Double-tap ⌘")

        menu = NSMenu.alloc().init()

        # Title with mode indicator
        mode = "Groq API" if USE_GROQ else "Local (MLX)"
        menu.addItemWithTitle_action_keyEquivalent_(f"STT - {mode}", None, "")
        menu.addItem_(NSMenuItem.separatorItem())

        # API Key setting
        api_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Set Groq API Key...", b"showApiKeyDialog:", ""
        )
        api_item.setTarget_(self)
        menu.addItem_(api_item)

        # Mode toggle
        mode_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Switch to Local (MLX)" if USE_GROQ else "Switch to Groq",
            b"toggleMode:", ""
        )
        mode_item.setTarget_(self)
        menu.addItem_(mode_item)

        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_("Quit", "terminate:", "q")
        self._status_item.setMenu_(menu)
        self._menu = menu

        screen = NSScreen.mainScreen().frame()
        sw = screen.size.width

        self._bar_window = self._make_overlay(
            NSMakeRect(screen.origin.x, screen.origin.y, sw, MIN_BAR_HEIGHT)
        )
        self._bar_view = BottomBarView.alloc().initWithFrame_(
            NSMakeRect(0, 0, sw, MIN_BAR_HEIGHT)
        )
        self._bar_window.setContentView_(self._bar_view)
        self._screen_w = sw

        mask = (1 << 12) | (1 << 10) | (1 << 11)
        self._event_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_event
        )

        threading.Thread(target=self._warmup, daemon=True).start()

        # If no API key, show setup dialog on first launch
        if not get_api_key():
            self.performSelector_withObject_afterDelay_(b"showFirstRunDialog:", None, 0.5)

    @objc.python_method
    def _make_overlay(self, rect):
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, 2, False,
        )
        win.setLevel_(25)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(False)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
        )
        win.setAlphaValue_(0.0)
        return win

    @objc.python_method
    def _handle_event(self, event):
        if _pasting:
            return
        etype = event.type()
        if etype == 12:
            flags = event.modifierFlags()
            cmd_now = bool(flags & CMD_FLAG)
            if cmd_now and not self._cmd_is_down:
                self._cmd_is_down = True
                self._cmd_was_solo = True
            elif not cmd_now and self._cmd_is_down:
                self._cmd_is_down = False
                if self._cmd_was_solo:
                    # If recording, single tap stops it
                    if self._recording:
                        threading.Thread(target=self._toggle, daemon=True).start()
                        self._last_cmd_release = 0.0
                    else:
                        # Not recording - need double tap to start
                        now = time.time()
                        if now - self._last_cmd_release < DOUBLE_TAP_WINDOW:
                            self._last_cmd_release = 0.0
                            threading.Thread(target=self._toggle, daemon=True).start()
                        else:
                            self._last_cmd_release = now
        elif etype in (10, 11) and self._cmd_is_down:
            self._cmd_was_solo = False

    @objc.python_method
    def _make_dot(self, active):
        s = 18.0
        img = NSImage.alloc().initWithSize_(NSMakeSize(s, s))
        img.lockFocus()
        if active:
            NSColor.colorWithRed_green_blue_alpha_(0.18, 0.85, 0.45, 1.0).setFill()
        else:
            NSColor.colorWithRed_green_blue_alpha_(0.5, 0.5, 0.5, 1.0).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(3, 3, 12, 12)).fill()
        img.unlockFocus()
        img.setTemplate_(not active)
        return img

    @objc.python_method
    def _warmup(self):
        if USE_GROQ:
            print("  Using Groq API (fast, no RAM usage)")
            get_groq()  # init client
            print("  Ready! Double-tap ⌘ to dictate.")
        else:
            print("  Loading local model (this may take a moment)...")
            whisper = get_whisper()
            dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
            whisper.transcribe(dummy, path_or_hf_repo=MODEL, fp16=False, verbose=False)
            print("  Model ready! Double-tap ⌘ to dictate.")
        self._model_ready = True
        # Show brief "Ready" notification
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"doShowReady:", None, False
        )

    def doShowReady_(self, _):
        """Flash a brief 'STT Ready' notification at bottom."""
        self._bar_view.set_state("done")
        self._bar_view.set_text("STT Ready - Double-tap \u2318 to dictate")
        self._bar_window.orderFrontRegardless()
        self._bar_window.setAlphaValue_(1.0)
        # Fade out after 2 seconds
        if self._hide_timer:
            self._hide_timer.invalidate()
        self._hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, b"doHide:", None, False
        )

    # ── Settings UI ──

    def showFirstRunDialog_(self, _):
        """Show welcome dialog with API key input on first launch."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Welcome to STT")
        alert.setInformativeText_(
            "Speech-to-Text powered by Groq.\n\n"
            "Enter your Groq API key to get started.\n"
            "Get one free at console.groq.com/keys\n\n"
            "Without a key, local mode (MLX Whisper) will be used."
        )
        alert.addButtonWithTitle_("Save & Start")
        alert.addButtonWithTitle_("Use Local Mode")

        text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 24))
        text_field.setPlaceholderString_("gsk_...")
        alert.setAccessoryView_(text_field)

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            new_key = text_field.stringValue().strip()
            if new_key:
                settings = load_settings()
                settings["groq_api_key"] = new_key
                save_settings(settings)
                global USE_GROQ, GROQ_API_KEY
                GROQ_API_KEY = new_key
                USE_GROQ = True
                self._update_menu()
                print("  API key saved! Using Groq.")
            else:
                print("  No key entered, using local mode.")
        else:
            print("  Using local mode (MLX Whisper).")

    def showApiKeyDialog_(self, sender):
        """Show a dialog to enter Groq API key."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Groq API Key")
        alert.setInformativeText_("Enter your Groq API key.\nGet one free at console.groq.com/keys")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")

        text_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 340, 24))
        current_key = get_api_key()
        if current_key:
            text_field.setStringValue_(current_key)
        text_field.setPlaceholderString_("gsk_...")
        alert.setAccessoryView_(text_field)

        # Bring app to front for dialog
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            new_key = text_field.stringValue().strip()
            if new_key:
                settings = load_settings()
                settings["groq_api_key"] = new_key
                save_settings(settings)
                global USE_GROQ, GROQ_API_KEY
                GROQ_API_KEY = new_key
                USE_GROQ = True
                self._update_menu()
                print(f"  API key saved!")

    def toggleMode_(self, sender):
        """Toggle between Groq and Local mode."""
        global USE_GROQ
        if USE_GROQ:
            USE_GROQ = False
            print("  Switched to Local (MLX) mode")
            # Load model if not already loaded
            if self._model_ready:
                threading.Thread(target=self._warmup_local, daemon=True).start()
        else:
            key = get_api_key()
            if not key:
                self.showApiKeyDialog_(None)
                return
            USE_GROQ = True
            print("  Switched to Groq API mode")
        self._update_menu()

    @objc.python_method
    def _warmup_local(self):
        print("  Loading local model...")
        whisper = get_whisper()
        dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
        whisper.transcribe(dummy, path_or_hf_repo=MODEL, fp16=False, verbose=False)
        print("  Local model ready!")

    @objc.python_method
    def _update_menu(self):
        """Update menu items to reflect current mode."""
        mode = "Groq API" if USE_GROQ else "Local (MLX)"
        self._menu.itemAtIndex_(0).setTitle_(f"STT - {mode}")
        toggle_title = "Switch to Local (MLX)" if USE_GROQ else "Switch to Groq"
        self._menu.itemAtIndex_(3).setTitle_(toggle_title)

    # ── UI updates (main thread) ──

    def doShow_(self, _):
        if self._hide_timer:
            self._hide_timer.invalidate()
            self._hide_timer = None
        self._bar_view.set_state("recording")
        self._bar_view.set_text("")
        # Reset to minimum height
        self._bar_window.setFrame_display_(
            NSMakeRect(0, 0, self._screen_w, MIN_BAR_HEIGHT), True
        )
        self._bar_view.setFrame_(NSMakeRect(0, 0, self._screen_w, MIN_BAR_HEIGHT))
        self._bar_window.orderFrontRegardless()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        self._bar_window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()
        self._anim_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, self, b"animTick:", None, True
        )
        self._status_item.button().setImage_(self._rec_image)

    def doHide_(self, _):
        if self._anim_timer:
            self._anim_timer.invalidate()
            self._anim_timer = None
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.5)
        self._bar_window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        self._status_item.button().setImage_(self._idle_image)
        self._hide_timer = None

    def doUpdateText_(self, text):
        self._bar_view.set_text(text)
        self._resize_bar()

    def doShowDone_(self, text):
        self._bar_view.set_state("done")
        self._bar_view.set_text(text)
        self._resize_bar()
        # Schedule fade out
        self._hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            PREVIEW_LINGER, self, b"doHide:", None, False
        )

    @objc.python_method
    def _resize_bar(self):
        """Resize the bar window to fit the text content."""
        desired_h = self._bar_view.get_desired_height()
        frame = self._bar_window.frame()
        if abs(frame.size.height - desired_h) < 2:
            return
        new_frame = NSMakeRect(0, 0, self._screen_w, desired_h)
        self._bar_window.setFrame_display_animate_(new_frame, True, True)
        self._bar_view.setFrame_(NSMakeRect(0, 0, self._screen_w, desired_h))

    def animTick_(self, timer):
        if self._bar_view:
            self._bar_view.tick()

    # ── Recording logic ──

    @objc.python_method
    def _toggle(self):
        if not self._model_ready:
            print("  Model still loading, please wait...")
            return
        with self._lock:
            if not self._recording:
                self._start_recording()
            else:
                self._stop_recording()

    @objc.python_method
    def _start_recording(self):
        self._frames = []
        self._stop_event.clear()
        self._recording = True
        self._live_text = ""
        # Remember which app the user was in so we can paste back to it
        self._target_app = get_frontmost_app()
        print(f"  Target app: {self._target_app}")

        def audio_cb(indata, frame_count, time_info, status):
            if not self._stop_event.is_set():
                self._frames.append(indata.copy())
                rms = float(np.sqrt(np.mean(indata ** 2)))
                if self._bar_view:
                    self._bar_view.set_audio_level(min(rms * 18.0, 1.0))

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", callback=audio_cb, blocksize=1024,
        )
        self._stream.start()
        print("  🎙 Recording...")
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"doShow:", None, False
        )
        # Start live transcription loop
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()

    @objc.python_method
    def _live_loop(self):
        """Periodically transcribe to show live preview."""
        while not self._stop_event.is_set():
            time.sleep(LIVE_INTERVAL)
            if self._stop_event.is_set():
                break
            if not self._frames:
                continue
            audio = np.concatenate(list(self._frames), axis=0).flatten()
            if len(audio) / SAMPLE_RATE < 0.5:
                continue
            try:
                # Pause animation for local MLX to avoid Metal GPU conflict
                if not USE_GROQ:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"doPauseAnim:", None, True
                    )
                text = transcribe_audio(audio)
                if not USE_GROQ:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"doResumeAnim:", None, True
                    )
                if text:
                    self._live_text = text
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"doUpdateText:", text, False
                    )
                    print(f"  [live] {text}")
            except Exception as e:
                if not USE_GROQ:
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        b"doResumeAnim:", None, True
                    )
                print(f"  [live error] {e}")

    @objc.python_method
    def _stop_recording(self):
        self._recording = False
        self._stop_event.set()
        self._stream.stop()
        self._stream.close()
        print("  Stopping...")

        # Wait for live transcription to finish before starting final
        if self._live_thread:
            self._live_thread.join(timeout=3)
            self._live_thread = None

        if not self._frames:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"doHide:", None, False
            )
            return

        audio = np.concatenate(self._frames, axis=0).flatten()
        if len(audio) / SAMPLE_RATE < 0.3:
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"doHide:", None, False
            )
            return

        # Stop animation
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"doStopAnim:", None, True
        )

        print("  Transcribing final...")
        threading.Thread(target=self._final_transcribe, args=(audio,), daemon=True).start()

    def doStopAnim_(self, _):
        if self._anim_timer:
            self._anim_timer.invalidate()
            self._anim_timer = None

    def doPauseAnim_(self, _):
        if self._anim_timer:
            self._anim_timer.invalidate()
            self._anim_timer = None

    def doResumeAnim_(self, _):
        if self._anim_timer is None and self._recording:
            self._anim_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / FPS, self, b"animTick:", None, True
            )

    @objc.python_method
    def _final_transcribe(self, audio):
        try:
            if not USE_GROQ:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"doPauseAnim:", None, True
                )
            text = transcribe_audio(audio)
            print(f"  Final: {text}")
            if text:
                # Show "done" state with checkmark
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"doShowDone:", text, False
                )
                # Paste into the app user was in when they started recording
                time.sleep(0.15)
                print(f"  Pasting to {self._target_app}: '{text[:50]}...'")
                paste_at_cursor(text, self._target_app)
                print("  Pasted!")
            else:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"doHide:", None, False
                )
        except Exception as e:
            print(f"  Error: {e}")
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"doHide:", None, False
            )


def kill_existing():
    """Kill any other running instances of this app."""
    import signal
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "stt.py"],
            capture_output=True, text=True
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                pid = int(line.strip())
                if pid != my_pid:
                    os.kill(pid, signal.SIGTERM)
                    print(f"  Killed existing instance (PID {pid})")
    except Exception:
        pass


def main():
    kill_existing()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == "__main__":
    main()

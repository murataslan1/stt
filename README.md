# STT — Speech to Text

System-wide voice dictation for macOS, Windows, and Linux. Double-tap a modifier key, talk, tap once to paste the transcription wherever your cursor is.

Powered by [Groq](https://console.groq.com/keys)'s Whisper API (fast, free tier) with a local MLX Whisper fallback on Apple Silicon.

![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey) ![license](https://img.shields.io/badge/license-MIT-blue)

## How it works

- **macOS**: double-tap ⌘ to start recording, single-tap ⌘ to stop and paste.
- **Windows**: double-tap Ctrl to start, single-tap Ctrl to stop and paste.
- **Linux**: double-tap Ctrl to start, single-tap Ctrl to stop and paste. (X11 recommended — Wayland has caveats, see below.)

A thin bar animates at the bottom of your screen while it listens. As you speak it streams live transcription; when you stop, the final polished text is pasted at the cursor in whichever app you were in.

## Install

### Option 1 — prebuilt macOS app (easiest)

Grab `STT-macOS.zip` from the [latest release](../../releases/latest), unzip, drag `STT.app` into `/Applications`. First launch will ask for your Groq API key.

macOS will warn about an unsigned app — right-click → Open → Open, or run:
```bash
xattr -dr com.apple.quarantine /Applications/STT.app
```

### Option 2 — from source (macOS)

```bash
git clone https://github.com/murataslan1/stt.git
cd stt/macos
./install.sh           # installs Python deps
python3 stt.py         # or double-click STT.app
```

Grant **Accessibility** and **Microphone** permission when prompted:
System Settings → Privacy & Security → Accessibility → add Terminal (or STT.app).

### Option 3 — Windows (from source)

```cmd
git clone https://github.com/murataslan1/stt.git
cd stt\windows
pip install -r requirements.txt
python stt_windows.py
```

Or build a standalone `.exe`:
```cmd
build.bat
```
The resulting `dist\STT.exe` is self-contained — double-click to run.

### Option 4 — Linux (from source)

```bash
git clone https://github.com/murataslan1/stt.git
cd stt/linux
./install.sh            # installs python-tk, portaudio, xclip, xdotool + pip deps
python3 stt_linux.py
```

Or per-distro manually:
```bash
# Debian/Ubuntu
sudo apt install python3-tk portaudio19-dev xclip xdotool
# Fedora
sudo dnf install python3-tkinter portaudio-devel xclip xdotool
# Arch
sudo pacman -S tk portaudio xclip xdotool

pip install --user -r requirements.txt
python3 stt_linux.py
```

**Wayland note**: `pynput` can't capture global key events on most Wayland compositors. Workarounds: (1) use an X11 session, (2) run under XWayland (GNOME/KDE do this for X11 apps automatically — but the global listener still needs X), or (3) bind a compositor shortcut that runs `pkill -USR1 python3` or similar to trigger recording. The paste side auto-detects Wayland and uses `wl-copy` + `wtype` / `ydotool` if available.

## API key

On first launch, a dialog asks for a Groq API key. Get one free at [console.groq.com/keys](https://console.groq.com/keys).

The key is stored in `~/.config/stt/settings.json` (both platforms). You can also set `GROQ_API_KEY` as an env var.

If you skip the key on macOS, the app falls back to local MLX Whisper (downloads ~1.5GB model on first run; no network needed after).

## Usage

| Action | macOS | Windows | Linux |
|---|---|---|---|
| Start recording | Double-tap ⌘ | Double-tap Ctrl | Double-tap Ctrl |
| Stop & paste | Single-tap ⌘ | Single-tap Ctrl | Single-tap Ctrl |

The menu bar icon (macOS) lets you switch between Groq and local mode, or update the API key.

## Customization

All configurable values live at the top of `stt.py` / `stt_windows.py`:

- `DOUBLE_TAP_WINDOW` — max seconds between taps (default 0.4)
- `LIVE_INTERVAL` — how often live transcription refreshes (default 2.0s)
- `PREVIEW_LINGER` — how long the final text stays visible (default 3.0s)
- `MODEL` (macOS) — MLX model for local mode
- `GROQ_MODEL` — Groq model; default is `whisper-large-v3-turbo`

Want a different hotkey? Change `CMD_FLAG` (macOS) or the `keyboard.Key.ctrl*` checks (Windows) in the event handler.

Want a different STT backend? Replace `transcribe_audio()` — it takes a numpy float32 array and returns a string.

## Structure

```
stt/
├── macos/
│   ├── stt.py               # main app
│   ├── requirements.txt
│   ├── install.sh
│   └── STT.app/             # double-clickable launcher
├── windows/
│   ├── stt_windows.py
│   ├── requirements.txt
│   └── build.bat            # builds standalone .exe
└── linux/
    ├── stt_linux.py
    ├── requirements.txt
    ├── install.sh
    └── stt.desktop          # menu/launcher entry
```

## Privacy

- Audio is sent to Groq only while recording.
- Nothing is stored on disk except the API key in `settings.json`.
- Local MLX mode (macOS) keeps everything on-device.

## License

MIT — see [LICENSE](LICENSE).

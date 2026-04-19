#!/usr/bin/env bash
# STT installer for Linux (Debian/Ubuntu, Fedora, Arch)
set -e

echo "================================"
echo "  STT - Speech to Text (Linux)"
echo "================================"
echo

# Detect package manager and install system deps
install_system() {
    if command -v apt-get >/dev/null; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip python3-tk portaudio19-dev xclip xdotool
    elif command -v dnf >/dev/null; then
        sudo dnf install -y python3 python3-pip python3-tkinter portaudio-devel xclip xdotool
    elif command -v pacman >/dev/null; then
        sudo pacman -S --needed --noconfirm python python-pip tk portaudio xclip xdotool
    else
        echo "Unknown distro. Install manually: python3, python3-tk, portaudio, xclip, xdotool."
    fi
}

echo "Installing system packages (python3-tk, portaudio, xclip, xdotool)..."
install_system

echo
echo "Installing Python packages..."
python3 -m pip install --user -r "$(dirname "$0")/requirements.txt"

echo
echo "Done!"
echo "  Run: python3 $(dirname "$(realpath "$0")")/stt_linux.py"
echo
echo "  Double-tap Ctrl to start, single tap to stop and paste."
echo "  Get a free Groq API key at: https://console.groq.com/keys"
echo
echo "  Optional — install the .desktop entry:"
echo "    cp stt.desktop ~/.local/share/applications/"

#!/bin/bash
echo "================================"
echo "  STT - Speech to Text Setup"
echo "================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Installing via Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew install python
fi

echo "Installing dependencies..."
pip3 install --break-system-packages -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt

echo ""
echo "IMPORTANT: Grant Accessibility permission!"
echo "  System Settings > Privacy & Security > Accessibility"
echo "  Add Terminal and Python.app"
echo ""
echo "Done! You can now:"
echo "  1. Double-click STT.app to run"
echo "  2. Or run: python3 stt.py"
echo ""
echo "  Double-tap Command to start recording"
echo "  Single tap Command to stop and paste"
echo ""
echo "First launch will ask for your Groq API key."
echo "Get one free at: https://console.groq.com/keys"

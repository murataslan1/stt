@echo off
echo ================================
echo   STT - Speech to Text Builder
echo ================================
echo.

echo Installing dependencies...
pip install -r requirements.txt pyinstaller

echo.
echo Building STT.exe...
pyinstaller --name "STT" --onefile --noconsole --noconfirm ^
    --hidden-import=groq ^
    --hidden-import=httpx ^
    --hidden-import=httpcore ^
    --hidden-import=sounddevice ^
    --hidden-import=numpy ^
    --hidden-import=pynput ^
    --hidden-import=pyperclip ^
    stt_windows.py

echo.
echo ================================
echo   Done! STT.exe is in dist\
echo   Double-tap Ctrl to start
echo   Single tap Ctrl to stop
echo ================================
pause

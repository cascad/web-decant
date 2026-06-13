@echo off
REM Convenience wrapper so you can run `decant get <url>` from anywhere on this machine.
"%~dp0.venv\Scripts\python.exe" "%~dp0decant.py" %*

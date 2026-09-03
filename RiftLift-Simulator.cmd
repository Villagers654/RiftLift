@echo off
setlocal
call "%~dp0RiftLift.cmd" simulate --runtime "%~dp0portable\tools\SteamVR"
exit /b %errorlevel%

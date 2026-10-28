@echo off
cd /d "%~dp0"
echo [gateway] starting model-gateway on http://127.0.0.1:8317 ...
echo [gateway] close this window to stop the gateway.
"dist\model-gateway.exe"

@echo off
rem One-click start for Windows: creates venv, installs deps, starts the gateway.
rem Usage:
rem   run.bat                    start the gateway
rem   run.bat qoder-login        Qoder device login (one-time)
rem   run.bat qoder-status       Qoder account / quota status
rem Requires Python 3.10+ on PATH (https://www.python.org/downloads/).
setlocal
cd /d %~dp0

if not exist .venv (
  echo [run] creating .venv
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [run] installing dependencies
pip install -q -r requirements.txt
if errorlevel 1 (
  echo [run] default index failed, retrying with pypi.org
  pip install -q -i https://pypi.org/simple -r requirements.txt
)

echo [run] starting gateway (Ctrl+C to stop)
python -m gateway %*
endlocal

@echo off
rem Stop uvicorn started by start_server.bat (reads PID from .uvicorn.pid)
setlocal
cd /d "%~dp0.."

if not exist ".uvicorn.pid" (
  echo .uvicorn.pid not found. Nothing to stop.
  exit /b 1
)

powershell -NoLogo -NoProfile -Command ^
  "$pid = Get-Content '.uvicorn.pid' | Select-Object -First 1; " ^
  "if ($pid) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Write-Host ('Stopped PID {0}' -f $pid); } else { Write-Host 'PID is empty'; exit 1 }; " ^
  "Remove-Item '.uvicorn.pid' -Force"

endlocal

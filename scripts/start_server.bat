@echo off
rem Start uvicorn locally (Windows). Requires venv at .venv and DATABASE_URL set in environment or .env loaded by app.
setlocal
cd /d "%~dp0.."

powershell -NoLogo -NoProfile -Command ^
  "$p = Start-Process -FilePath '.venv\\Scripts\\python.exe' -ArgumentList '-m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload' -WorkingDirectory '%CD%' -PassThru; " ^
  "Set-Content -Path '.uvicorn.pid' -Value $p.Id; " ^
  "Write-Host ('Started uvicorn PID {0} on port 8100' -f $p.Id)"

endlocal

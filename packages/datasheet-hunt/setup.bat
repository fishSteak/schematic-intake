@echo off
setlocal
cd /d "%~dp0"
echo [1/3] pip install AnySearch deps...
python -m pip install -r "%CD%\requirements.txt"
if errorlevel 1 exit /b 1

if not exist "%CD%\vendor\anysearch\.env" (
  echo [2/3] create vendor\anysearch\.env from example ^(fill ANYSEARCH_API_KEY^)
  copy /Y "%CD%\vendor\anysearch\.env.example" "%CD%\vendor\anysearch\.env" >nul
) else (
  echo [2/3] vendor\anysearch\.env already exists
)

echo [3/3] probe AnySearch bridge...
set PYTHONPATH=%CD%
python -c "from datasheet_hunt import anysearch_available; from datasheet_hunt.anysearch_bridge import resolve_anysearch_cmd; print('anysearch_available=', anysearch_available()); print('cmd=', resolve_anysearch_cmd())"
echo.
echo Next: edit vendor\anysearch\.env and set ANYSEARCH_API_KEY=...
echo Then: run_example.bat   or   python -m datasheet_hunt --out-dir out --mpn LM393DR
endlocal

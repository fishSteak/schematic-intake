@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=%CD%
python -m datasheet_hunt --out-dir "%CD%\out_demo" --mpn-file "%CD%\examples\mpns.txt" --url-hints "%CD%\examples\url_hints.json" --no-ddg %*
echo.
echo See out_demo\datasheets\library.json
endlocal

@echo off
python -m mission_planner.cli --tasks examples\tasks.json --uavs examples\uavs.json --output outputs\plan.json
if errorlevel 1 exit /b 1
python -m mission_planner.visualize --plan outputs\plan.json --output outputs\report.html
if errorlevel 1 exit /b 1
echo Generated:
echo   outputs\plan.json
echo   outputs\report.html

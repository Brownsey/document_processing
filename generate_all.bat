@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "result=0"

for %%C in (client_01_clean client_02_medium client_03_hard client_04_stretch) do (
    echo Generating %%C...
    call uv run --locked python -m agent_pipeline.generate --client %%C --provider openrouter --model openai/gpt-6-luna %*
    if errorlevel 1 (
        echo FAILED: %%C
        set "result=1"
    )
)

if "%result%"=="0" (
    echo All four reports generated in outputs.
) else (
    echo One or more reports failed. Check the messages above and outputs diagnostics.
)
popd
exit /b %result%

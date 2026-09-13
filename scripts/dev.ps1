$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uvicorn app.main:app --reload

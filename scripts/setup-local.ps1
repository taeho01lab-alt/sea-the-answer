param([string]$PgBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
$UvExecutable = if ($UvCommand) { $UvCommand.Source } elseif (Test-Path '.tools/bin/uv.exe') { (Resolve-Path '.tools/bin/uv.exe').Path } else { throw 'Install uv first: python -m pip install uv' }
& $UvExecutable sync --frozen --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm.cmd ci --prefix web
if ($LASTEXITCODE -ne 0) { throw 'Next.js dependency installation failed.' }
& .\.venv\Scripts\python.exe -m service.manage local-db --pg-bin $PgBin
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL setup failed.' }
& .\.venv\Scripts\python.exe -m service.manage bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Sample setup failed.' }
Write-Host 'Ready. Run .\start-design.cmd. Credentials: data\initial-login.txt'

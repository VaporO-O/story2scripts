param(
    [ValidateSet('web','worker')][string]$Mode = 'web',
    [string]$Python = 'python',
    [int]$Port = 8000,
    [int]$RedisPort = 6379,
    [int]$Concurrency = 2
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $projectRoot
$env:STORY2SCRIPT_TASK_BACKEND = 'redis'
if (-not $env:STORY2SCRIPT_REDIS_URL) { $env:STORY2SCRIPT_REDIS_URL = "redis://127.0.0.1:$RedisPort/0" }
if (-not $env:STORY2SCRIPT_REDIS_NAMESPACE) { $env:STORY2SCRIPT_REDIS_NAMESPACE = 'story2script' }
if (-not $env:STORY2SCRIPT_JOBS_DB) { $env:STORY2SCRIPT_JOBS_DB = Join-Path $projectRoot '.story2script/jobs.db' }
$env:STORY2SCRIPT_CACHE_BACKEND = 'redis'
if (-not $env:STORY2SCRIPT_LLM_RPM) { $env:STORY2SCRIPT_LLM_RPM = '60' }
if (-not $env:STORY2SCRIPT_EMBED_RPM) { $env:STORY2SCRIPT_EMBED_RPM = '120' }
if ($Mode -eq 'worker') {
    & $Python -m story2script.worker --concurrency $Concurrency
} else {
    & $Python -m uvicorn story2script.main:app --host 127.0.0.1 --port $Port
}
exit $LASTEXITCODE

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-RunningServices {
    param([string[]]$ExpectedServices)

    $running = docker compose ps --services --status running
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read running services from Docker Compose."
    }

    $runningSet = @{}
    foreach ($service in $running) {
        $runningSet[$service.Trim()] = $true
    }

    foreach ($service in $ExpectedServices) {
        if (-not $runningSet.ContainsKey($service)) {
            throw "Service '$service' is not running."
        }
        Write-Host "[OK] Service running: $service"
    }
}

function Assert-Http200 {
    param(
        [string]$Name,
        [string]$Url
    )

    $tempFile = [System.IO.Path]::GetTempFileName()
    try {
        $status = curl.exe -sS -L -o $tempFile -w "%{http_code}" $Url
        if ($LASTEXITCODE -ne 0) {
            throw "curl exited with code $LASTEXITCODE"
        }
        if ([int]$status -ne 200) {
            throw "$Name returned HTTP $status, expected 200."
        }

        Write-Host "[OK] $Name returned 200"
        return Get-Content -LiteralPath $tempFile -Raw
    }
    finally {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Verifying unified local-llm stack..."

Assert-RunningServices -ExpectedServices @(
    "ollama",
    "openwebui",
    "redis",
    "searxng",
    "openedai-speech",
    "local-deep-research"
)

$ollamaJson = Assert-Http200 -Name "Ollama tags" -Url "http://localhost:11434/api/tags" | ConvertFrom-Json
if (-not $ollamaJson.models) {
    throw "Ollama returned no installed models."
}
Write-Host "[OK] Ollama models detected: $($ollamaJson.models.Count)"

$searxJson = Assert-Http200 -Name "SearXNG search" -Url "http://localhost:8090/search?q=latest+technology+news&format=json" | ConvertFrom-Json
if ($null -eq $searxJson.results) {
    throw "SearXNG returned no results field."
}
Write-Host "[OK] SearXNG results field detected"

[void](Assert-Http200 -Name "Open WebUI" -Url "http://localhost:8080")
$researchHealth = Assert-Http200 -Name "Custom Local Deep Research health" -Url "http://localhost:5000/api/health" | ConvertFrom-Json
if ($researchHealth.status -ne "ok") {
    throw "The custom Local Deep Research harness did not report a healthy status."
}
if (-not $researchHealth.ollama_reachable -or -not $researchHealth.searxng_reachable) {
    throw "The research harness cannot reach Ollama or SearXNG over the Compose network."
}
Write-Host "[OK] Custom research harness dependencies reachable"

Write-Host "All checks passed."

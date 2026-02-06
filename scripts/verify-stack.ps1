Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-RunningServices {
    param(
        [string[]]$ExpectedServices
    )

    $running = docker compose ps --services --status running
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read running services from docker compose."
    }

    $runningSet = @{}
    foreach ($svc in $running) {
        $runningSet[$svc.Trim()] = $true
    }

    foreach ($svc in $ExpectedServices) {
        if (-not $runningSet.ContainsKey($svc)) {
            throw "Service '$svc' is not running."
        }
        Write-Host "[OK] Service running: $svc"
    }
}

function Assert-Http200 {
    param(
        [string]$Name,
        [string]$Url
    )

    $tmpFile = [System.IO.Path]::GetTempFileName()

    try {
        $status = curl.exe -sS -L -o $tmpFile -w "%{http_code}" $Url
        if ($LASTEXITCODE -ne 0) {
            throw "curl exited with code $LASTEXITCODE"
        }
    }
    catch {
        throw "$Name request failed: $($_.Exception.Message)"
    }
    finally {
        if (-not (Test-Path $tmpFile)) {
            New-Item -ItemType File -Path $tmpFile | Out-Null
        }
    }

    $statusCode = [int]$status
    if ($statusCode -ne 200) {
        throw "$Name returned HTTP $statusCode, expected 200."
    }

    Write-Host "[OK] $Name returned 200"
    $content = Get-Content -Path $tmpFile -Raw
    Remove-Item -Force $tmpFile
    return [PSCustomObject]@{
        StatusCode = $statusCode
        Content = $content
    }
}

Write-Host "Verifying local-llm stack..."

Assert-RunningServices -ExpectedServices @(
    "ollama",
    "openwebui",
    "searxng",
    "local-deep-research"
)

$ollamaResponse = Assert-Http200 -Name "Ollama tags" -Url "http://localhost:11434/api/tags"
$tags = $ollamaResponse.Content | ConvertFrom-Json
if (-not $tags.models) {
    throw "Ollama returned no models."
}
Write-Host "[OK] Ollama models detected: $($tags.models.Count)"

$searxResponse = Assert-Http200 -Name "SearXNG search" -Url "http://localhost:8090/search?q=latest+technology+news&format=json"
$searxJson = $searxResponse.Content | ConvertFrom-Json
if (-not $searxJson.results) {
    throw "SearXNG returned no 'results' field."
}
Write-Host "[OK] SearXNG results field detected"

[void](Assert-Http200 -Name "Open WebUI" -Url "http://localhost:8080")
[void](Assert-Http200 -Name "Local Deep Research" -Url "http://localhost:5000")
[void](Assert-Http200 -Name "Local Deep Research Health" -Url "http://localhost:5000/api/v1/health")

Write-Host "All checks passed."

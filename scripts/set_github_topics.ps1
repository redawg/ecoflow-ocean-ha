#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Set GitHub repository topics required by HACS validation.

.DESCRIPTION
  HACS requires at least one topic on the repository. GitHub Actions GITHUB_TOKEN
  cannot set topics, so this must be run once with a personal access token.

.PARAMETER Token
  GitHub PAT with repo admin access. Defaults to $env:GH_TOKEN or $env:GITHUB_TOKEN.

.EXAMPLE
  $env:GH_TOKEN = "ghp_..."
  ./scripts/set_github_topics.ps1
#>
param(
    [string]$Owner = "redawg",
    [string]$Repo = "ecoflow-ocean-ha",
    [string]$Token = $(if ($env:GH_TOKEN) { $env:GH_TOKEN } elseif ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { "" })
)

if (-not $Token) {
    throw "Set GH_TOKEN or GITHUB_TOKEN, or pass -Token with a PAT that can manage repository topics."
}

$headers = @{
    Authorization              = "Bearer $Token"
    Accept                     = "application/vnd.github+json"
    "X-GitHub-Api-Version"     = "2022-11-28"
}

$topics = @(
    "home-assistant",
    "hacs",
    "hacs-integration",
    "ecoflow",
    "power-ocean",
    "battery",
    "energy"
)

$body = @{ names = $topics } | ConvertTo-Json -Compress
$uri = "https://api.github.com/repos/$Owner/$Repo/topics"

$response = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -Body $body -ContentType "application/json"
Write-Host "Topics set: $($response.names -join ', ')"

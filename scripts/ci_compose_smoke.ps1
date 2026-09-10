# CI 第 3 层：compose 栈冒烟（起栈 → 探活 → 可选单题冒烟 → 落报告 → 清理）
#
# 用法（先演练，再正式）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci_compose_smoke.ps1 -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci_compose_smoke.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci_compose_smoke.ps1 -WithChatSmoke
#
# 说明：
# - 默认只做「起栈 + 探活」（qdrant /healthz、backend :18000/health），零 LLM 成本；
# - -WithChatSmoke 才会发一次真实问答请求（消耗 DashScope 额度），故默认关闭；
# - 失败也会尽力 stop 掉本次拉起的服务（-KeepStack 可保留现场）。
param(
    [switch]$DryRun,
    [switch]$WithChatSmoke,
    [switch]$KeepStack,
    [int]$HealthTimeoutSec = 120,
    [string]$OutFile = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
if (-not $OutFile) { $OutFile = Join-Path $root ("训练结果数据\ci_compose_smoke_" + $ts + ".json") }

$script:summary = [ordered]@{
    ts             = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    mode           = $(if ($DryRun) { 'DryRun' } else { 'real' })
    with_chat      = [bool]$WithChatSmoke
    steps          = @()
    qdrant_health  = $null
    backend_health = $null
    chat_smoke     = $null
    ok             = $false
}

function Add-Step([string]$name, [string]$result) {
    $script:summary.steps += [ordered]@{ step = $name; result = $result }
    Write-Output ("[smoke] " + $name + " -> " + $result)
}

function Wait-Http([string]$url, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { return $r.Content }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $null
}

$exitCode = 0
try {
    if ($DryRun) {
        Add-Step 'compose up -d qdrant backend frontend' 'DryRun（不执行）'
        Add-Step 'wait http://localhost:6333/healthz' 'DryRun（不执行）'
        Add-Step 'wait http://localhost:18000/health' 'DryRun（不执行）'
        Add-Step 'POST /chat（单题冒烟）' $(if ($WithChatSmoke) { 'DryRun（不执行）' } else { 'skip（未开 -WithChatSmoke）' })
        Add-Step 'compose stop' 'DryRun（不执行）'
        Write-Output '[smoke] DryRun 通过：参数与链路校验完成，未起容器、未调用 LLM'
    }
    else {
        docker compose up -d qdrant backend frontend | Out-Null
        Add-Step 'compose up -d qdrant backend frontend' 'done'

        $script:summary.qdrant_health = Wait-Http 'http://localhost:6333/healthz' $HealthTimeoutSec
        Add-Step 'qdrant /healthz' $(if ($script:summary.qdrant_health) { 'ok' } else { 'TIMEOUT' })

        $script:summary.backend_health = Wait-Http 'http://localhost:18000/health' $HealthTimeoutSec
        Add-Step 'backend /health' $(if ($script:summary.backend_health) { 'ok' } else { 'TIMEOUT' })

        if ($WithChatSmoke) {
            try {
                $body = @{ question = '云南白药2024年的营业收入是多少？'; mode = 'agent'; user_id = 'ci_smoke' } | ConvertTo-Json
                $resp = Invoke-WebRequest -Uri 'http://localhost:18000/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 180 -UseBasicParsing
                $script:summary.chat_smoke = $resp.StatusCode
                Add-Step 'POST /chat（单题冒烟）' ("HTTP " + $resp.StatusCode)
            } catch {
                $script:summary.chat_smoke = ("error: " + $_.Exception.Message)
                Add-Step 'POST /chat（单题冒烟）' 'FAILED'
            }
        } else {
            Add-Step 'POST /chat（单题冒烟）' 'skip（未开 -WithChatSmoke）'
        }

        $script:summary.ok = [bool]($script:summary.qdrant_health -and $script:summary.backend_health)
        if (-not $script:summary.ok) { $exitCode = 1 }
    }
}
finally {
    if (-not $DryRun -and -not $KeepStack) {
        try { docker compose stop qdrant backend frontend | Out-Null; Add-Step 'compose stop' 'done' }
        catch { Add-Step 'compose stop' 'error' }
    }
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
        ($script:summary | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $OutFile -Encoding UTF8
        Write-Output ("[smoke] 报告：" + $OutFile)
    }
}
exit $exitCode
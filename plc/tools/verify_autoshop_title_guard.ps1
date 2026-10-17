param(
    [string]$SourcePath = (Join-Path $PSScriptRoot 'autoshop_ui.ps1'),
    [string]$EvidencePath = ''
)

# Parse the production script, then execute only its pure validation function.
# Never dot-source autoshop_ui.ps1: its top-level code accesses the real GUI.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$parseTokens = $null
$parseErrors = $null
$scriptAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $SourcePath, [ref]$parseTokens, [ref]$parseErrors)
if($parseErrors.Count -ne 0){throw ($parseErrors | Out-String)}
$guardFunctions = @($scriptAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-AutoShopTitle'
}, $true))
if($guardFunctions.Count -ne 1){throw 'Expected one production Assert-AutoShopTitle function.'}
Invoke-Expression $guardFunctions[0].Extent.Text

$expected = 'E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV'
$prefix = 'AutoShop V4.12.0.2  '
$cases = @(
    @{name='clean MAIN accepted'; title=($prefix+$expected+' - [MAIN]'); main=$true; clean=$true; accept=$true},
    @{name='SBR write rejected'; title=($prefix+$expected+' - [SBR_001]'); main=$true; clean=$true; accept=$false},
    @{name='dirty MAIN write rejected'; title=($prefix+$expected+' - [MAIN *]'); main=$true; clean=$true; accept=$false},
    @{name='compact dirty MAIN write rejected'; title=($prefix+$expected+' - [MAIN*]'); main=$true; clean=$true; accept=$false},
    @{name='project prefix rejected'; title=($prefix+$expected+'_OTHER - [MAIN]'); main=$true; clean=$true; accept=$false},
    @{name='other project rejected'; title=($prefix+'E:\PLCProjects\ORIGINAL - [MAIN]'); main=$false; clean=$false; accept=$false},
    @{name='case and path normalization accepted'; title=($prefix+'e:\plcprojects\PLCIDF_LAB_20261017\.\bess_e521_dev\ - [MAIN]'); main=$true; clean=$true; accept=$true},
    @{name='malformed title rejected'; title=($prefix+$expected+' - MAIN'); main=$false; clean=$false; accept=$false},
    @{name='log SBR identity accepted'; title=($prefix+$expected+' - [SBR_001]'); main=$false; clean=$false; accept=$true},
    @{name='save dirty MAIN identity accepted'; title=($prefix+$expected+' - [MAIN *]'); main=$false; clean=$false; accept=$true}
)
$results = @()
foreach($guardCase in $cases){
    $accepted=$false
    $rejection=$null
    try {
        Assert-AutoShopTitle -Title $guardCase.title -ExpectedProject $expected `
            -RequireMain:$guardCase.main -RequireClean:$guardCase.clean
        $accepted=$true
    } catch {$rejection=$_.Exception.Message}
    $results += [ordered]@{name=$guardCase.name; expected_accept=$guardCase.accept;
        actual_accept=$accepted; passed=($accepted -eq $guardCase.accept); rejection=$rejection}
}
$bytes=[System.IO.File]::ReadAllBytes($SourcePath)
$bom=$bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191
$passed=$bom -and @($results|Where-Object {!$_.passed}).Count -eq 0
$report=[ordered]@{schema_version=1; result=$(if($passed){'passed'}else{'failed'});
    timestamp_utc=[DateTime]::UtcNow.ToString('o'); source_sha256=(Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant();
    production_script_ast_valid=$true; utf8_bom_preserved=$bom;
    ui_automation_executed=$false; device_communication_executed=$false;
    cases=$results}
$json=$report|ConvertTo-Json -Depth 6
if($EvidencePath){[System.IO.File]::WriteAllText($EvidencePath,$json+[Environment]::NewLine,(New-Object System.Text.UTF8Encoding($false)))}
Write-Output $json
if(!$passed){exit 1}

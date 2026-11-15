param([string]$InputJson)

$ErrorActionPreference = 'Stop'

function Get-TargetPaths {
    param($Value)

    if ($Value -is [array]) {
        foreach ($item in $Value) { Get-TargetPaths $item }
    }
    elseif ($Value -is [PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            if ($property.Name -match '(?i)^(filePath|filePaths|fileUri|targetFileUri|resourcePath|path|uri|dirPath|includePattern|query|selectFiles)$') {
                @($property.Value)
            }
            elseif ($property.Name -eq 'input' -and $property.Value -is [string]) {
                foreach ($match in [regex]::Matches($property.Value, '(?m)^\*\*\* (?:(?:Add|Update|Delete) File: |Move to: )(.+)$')) {
                    $match.Groups[1].Value.Trim()
                }
            }
            else { Get-TargetPaths $property.Value }
        }
    }
}

function Get-PolicyDecision {
    param($Payload)

    if ($null -ne $Payload.toolCalls) {
        if ($Payload.toolCalls -isnot [array] -or $Payload.toolCalls.Count -eq 0) {
            return @('deny', 'Host tool-call payload must contain inspectable calls.')
        }
        $combined = @('allow', 'Host tool calls contain no recognized restricted operation.')
        foreach ($call in $Payload.toolCalls) {
            if (-not $call.name -or $null -eq $call.args) {
                return @('deny', 'Host tool call is missing its name or arguments.')
            }
            $nested = Get-PolicyDecision ([PSCustomObject]@{ tool_name = $call.name; tool_input = $call.args })
            if ($nested[0] -eq 'deny') { return $nested }
            if ($nested[0] -eq 'ask') { $combined = $nested }
        }
        return $combined
    }
    if (-not $Payload.tool_name -or $null -eq $Payload.tool_input) {
        return @('deny', 'Malformed tool input; safety policy could not inspect it.')
    }
    $tool = [string]$Payload.tool_name
    $inputObject = $Payload.tool_input
    if ($inputObject -is [string]) {
        if ($tool -match '(?i)(^|[.])(?:apply_patch|Edit)$') {
            $inputObject = [PSCustomObject]@{ input = $inputObject }
        }
        else {
            try { $inputObject = $inputObject | ConvertFrom-Json }
            catch { return @('deny', 'Unparseable tool input; safety policy could not inspect it.') }
        }
    }
    if ($inputObject -isnot [PSCustomObject]) {
        return @('deny', 'Tool input must be a JSON object.')
    }
    if ($tool -match '(?i)(^|[.])parallel$') {
        if (-not $inputObject.tool_uses) {
            return @('deny', 'Parallel tool input must contain inspectable nested calls.')
        }
        $combined = @('allow', 'Nested calls contain no recognized restricted operation.')
        foreach ($call in $inputObject.tool_uses) {
            $nested = Get-PolicyDecision ([PSCustomObject]@{ tool_name = $call.recipient_name; tool_input = $call.parameters })
            if ($nested[0] -eq 'deny') { return $nested }
            if ($nested[0] -eq 'ask') { $combined = $nested }
        }
        return $combined
    }
    $paths = @(Get-TargetPaths $inputObject)
    if ($tool -match '(?i)(read_file|view_image|create_file|insert_edit_into_file|apply_patch|file_search|grep_search|list_dir|edit_notebook_file|Edit)$' -and $paths.Count -eq 0) {
        return @('deny', 'File tool input has no inspectable target.')
    }
    $protected = '(?i)(^|[/\\\s])(?:\.env(?:[.\w-]*)?|bot_ledger\.sqlite3(?:-wal|-shm)?|bot_state\.json|trades\.csv|daily_pnl\.csv|bot\.log|bot\.lock)(?=$|[/\\\s*?\[]|:)'
    $policyApproval = $false
    foreach ($path in $paths) {
        $normalized = [Uri]::UnescapeDataString([string]$path).Replace('\', '/')
        if ($normalized -match $protected) {
            return @('deny', 'Direct secret or operational-state access is blocked. Use temporary fixtures; operators handle operational access outside the agent.')
        }
        if ($tool -match '(?i)edit|patch|create_file|delete|rename' -and
            $normalized -match '(?i)(\.github/(hooks/|workflows/|copilot-instructions\.md|instructions/)|scripts/(agent_guard|test_agent_guard|offline_tests|validate_ai_setup)\.|docs/(development|safety-invariants)\.md)') {
            $policyApproval = $true
        }
    }
    if ($policyApproval) {
        return @('ask', 'Changing safety policy or its enforcement requires explicit approval.')
    }
    if ($tool -match '(?i)terminal|execute|run|create_and_run_task|install|debug|profil|notebook.*(cell|package)|click_element|type_in_page|navigate_page|open_browser_page|fetch_webpage|github_repo|github_text_search|searchExtensions') {
        return @('ask', 'Execution or external interaction requires approval. Approve only the specific offline check; authenticated paper/live operations need separate task-specific authorization.')
    }
    if ($tool -match '(?i)alpaca|broker|order|account') {
        return @('ask', 'Broker/account integration requires explicit task-specific operator approval.')
    }
    return @('allow', 'No restricted operation identified by this defense-in-depth policy.')
}

try {
    if (-not $InputJson) { $InputJson = [Console]::In.ReadToEnd() }
    $payload = $InputJson | ConvertFrom-Json
    $decision = Get-PolicyDecision $payload
}
catch {
    $decision = @('deny', 'Invalid hook payload; safety policy failed closed.')
}

@{
    hookSpecificOutput = @{
        hookEventName = 'PreToolUse'
        permissionDecision = $decision[0]
        permissionDecisionReason = $decision[1]
    }
} | ConvertTo-Json -Depth 4 -Compress
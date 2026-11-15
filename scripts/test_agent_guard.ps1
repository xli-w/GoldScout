$ErrorActionPreference = 'Stop'
$guard = Join-Path $PSScriptRoot 'agent_guard.ps1'
$cases = @(
    @{ Tool = 'read_file'; Input = @{ filePath = 'bot/portfolio.py' }; Expected = 'allow' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'C:\Trader\.env' }; Expected = 'deny' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'file:///C:/Trader/%2Eenv.local' }; Expected = 'deny' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'bot_ledger.sqlite3-wal' }; Expected = 'deny' },
    @{ Tool = 'create_file'; Input = @{ filePath = 'bot_state.json' }; Expected = 'deny' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'C:\Trader\.env::$DATA' }; Expected = 'deny' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'C:\Trader\.env:stream' }; Expected = 'deny' },
    @{ Tool = 'apply_patch'; Input = @{ input = "*** Begin Patch`n*** Delete File: C:\Trader\trades.csv`n*** End Patch" }; Expected = 'deny' },
    @{ Tool = 'apply_patch'; Input = @{ input = "*** Begin Patch`n*** Update File: C:\Trader\scripts\agent_guard.ps1`n*** End Patch" }; Expected = 'ask' },
    @{ Tool = 'apply_patch'; Input = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** End Patch"; Expected = 'allow' },
    @{ Tool = 'apply_patch'; Input = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** Move to: .env`n*** End Patch"; Expected = 'deny' },
    @{ Tool = 'functions.apply_patch'; Input = "*** Begin Patch`n*** Update File: scripts\test_agent_guard.ps1`n+test`n*** End Patch"; Expected = 'ask' },
    @{ Tool = 'Edit'; Input = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** End Patch"; Expected = 'allow' },
    @{ Tool = 'Edit'; Input = @{ input = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** End Patch" }; Expected = 'allow' },
    @{ Tool = 'Edit'; Input = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** Move to: .env`n*** End Patch"; Expected = 'deny' },
    @{ Tool = 'Edit'; Input = "*** Begin Patch`n*** Update File: scripts\agent_guard.ps1`n+test`n*** End Patch"; Expected = 'ask' },
    @{ Tool = 'Edit'; Input = 'not-json'; Expected = 'deny' },
    @{ Tool = 'Edit'; Input = @{}; Expected = 'deny' },
    @{ Tool = 'run_in_terminal'; Input = @{ command = 'python scripts/offline_tests.py' }; Expected = 'ask' },
    @{ Tool = 'send_to_terminal'; Input = @{ command = 'python -m bot.main' }; Expected = 'ask' },
    @{ Tool = 'pylanceRunCodeSnippet'; Input = @{ codeSnippet = 'print(1)' }; Expected = 'ask' },
    @{ Tool = 'broker_submit_order'; Input = @{ symbol = 'SPY' }; Expected = 'ask' },
    @{ Tool = 'read_file'; Input = @{ filePath = 'docs/development.md' }; Expected = 'allow' },
    @{ Tool = 'pylanceLSP'; Input = @{ fileUri = '.env' }; Expected = 'deny' },
    @{ Tool = 'pylanceTypeAuthoring'; Input = @{ targetFileUri = 'file:///C:/Trader/.env' }; Expected = 'deny' },
    @{ Tool = 'grep_search'; Input = @{ query = '.'; includePattern = '**/.env*' }; Expected = 'deny' },
    @{ Tool = 'read_file'; Input = @{}; Expected = 'deny' },
    @{ Tool = 'multi_tool_use.parallel'; Input = @{ tool_uses = @(@{ recipient_name = 'functions.read_file'; parameters = @{ filePath = '.env' } }) }; Expected = 'deny' },
    @{ Tool = 'multi_tool_use.parallel'; Input = @{ tool_uses = @(@{ recipient_name = 'functions.run_in_terminal'; parameters = @{ command = 'python scripts/offline_tests.py' } }) }; Expected = 'ask' },
    @{ Tool = 'pylanceLSP'; Input = @{ params = @{ textDocument = @{ uri = 'file:///C:/Trader/.env' } } }; Expected = 'deny' },
    @{ Tool = 'get_errors'; Input = @{ filePaths = @('bot/main.py', '.env') }; Expected = 'deny' },
    @{ Tool = 'apply_patch'; Input = @{ input = "*** Begin Patch`n*** Update File: C:\Trader\scripts\agent_guard.ps1`n*** Delete File: C:\Trader\bot_ledger.sqlite3`n*** End Patch" }; Expected = 'deny' },
    @{ Tool = 'functions.fetch_webpage'; Input = @{ urls = @('https://example.invalid'); query = 'docs' }; Expected = 'ask' },
    @{ Tool = 'functions.github_repo'; Input = @{ repo = 'example/repo'; query = 'code' }; Expected = 'ask' },
    @{ Tool = 'functions.github_text_search'; Input = @{ scope = 'example/repo'; query = 'code' }; Expected = 'ask' }
)

foreach ($case in $cases) {
    $payload = @{ tool_name = $case.Tool; tool_input = $case.Input } | ConvertTo-Json -Depth 8 -Compress
    $response = & $guard -InputJson $payload | ConvertFrom-Json
    if ($response.hookSpecificOutput.permissionDecision -ne $case.Expected) {
        throw "Unexpected decision for $($case.Tool): expected $($case.Expected)"
    }
}
$hostSafePatch = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** End Patch"
$hostPayload = @{ sessionId = 'synthetic'; cwd = 'C:\Trader'; toolCalls = @(
    @{ id = '1'; name = 'functions.apply_patch'; args = $hostSafePatch }
) }
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'allow') {
    throw 'Host-wrapped raw patch was not inspected as an ordinary documentation edit.'
}
$hostProtectedPatch = "*** Begin Patch`n*** Add File: docs\research-note.md`n+safe`n*** Move to: .env`n*** End Patch"
$hostPayload.toolCalls[0].args = $hostProtectedPatch
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'Host-wrapped move into a protected path was not denied.'
}
$hostPayload.toolCalls[0].name = 'Edit'
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'Host-wrapped Edit alias move into a protected path was not denied.'
}
$hostPayload.toolCalls[0].args = $hostSafePatch
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'allow') {
    throw 'Host-wrapped Edit alias documentation patch was not inspected.'
}
$hostPayload.toolCalls = @(
    @{ id = '1'; name = 'functions.read_file'; args = @{ filePath = 'bot\risk_manager.py' } },
    @{ id = '2'; name = 'functions.read_file'; args = @{ filePath = '.env' } }
)
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'One safe host call masked a protected sibling call.'
}
$hostPayload.toolCalls = @(
    @{ id = '1'; name = 'functions.run_in_terminal'; args = @{ command = 'python -m bot.main' } },
    @{ id = '2'; name = 'functions.read_file'; args = @{ filePath = 'C:\Trader\.env' } }
)
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'A host ask decision masked a protected call that followed it.'
}
$hostPayload.toolCalls = @(
    @{ id = '1'; name = 'functions.read_file'; args = @{ filePath = 'C:\Trader\.env' } },
    @{ id = '2'; name = 'functions.run_in_terminal'; args = @{ command = 'python -m bot.main' } }
)
$response = & $guard -InputJson ($hostPayload | ConvertTo-Json -Depth 8 -Compress) | ConvertFrom-Json
if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
    throw 'A host ask decision masked a preceding protected call.'
}
foreach ($invalid in @('not-json', '{}', '{"tool_name":"read_file","tool_input":"not-json"}',
                      '{"tool_name":"read_file","tool_input":42}', '{"tool_name":"read_file","tool_input":[]}')) {
    $response = & $guard -InputJson $invalid | ConvertFrom-Json
    if ($response.hookSpecificOutput.permissionDecision -ne 'deny') {
        throw 'Malformed input did not fail closed'
    }
}
Write-Output "Agent guard: $($cases.Count + 14) policy checks passed."
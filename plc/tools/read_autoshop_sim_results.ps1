# Read-only verification of the AutoShop simulator, not a physical PLC client.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
$autoShop = Get-Process AutoShop | Select-Object -First 1
$windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$root=$windows|Where-Object {$_.Current.ProcessId -eq $autoShop.Id -and $_.Current.Name.StartsWith('AutoShop ') -and $_.Current.ClassName.StartsWith('Afx:')}|Select-Object -First 1
if(!$root -or $root.Current.Name -notmatch '\\BESS_E521_TEST(?:\s|\\|$)') {
    throw 'The expected BESS_E521_TEST project is not open; no request sent.'
}
$controls = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
if(!($controls | Where-Object {$_.Current.Name.Trim() -eq '离线调试'})) {
    throw 'AutoShop is not in offline simulator mode; no request sent.'
}
$listener = Get-NetTCPConnection -LocalPort 502 -State Listen -ErrorAction Stop
$daemon = Get-Process -Id $listener.OwningProcess
if($daemon.ProcessName -ne 'h5u_daemon' -or $daemon.Path -notmatch '\\AutoShop\\Simulator\\h5u_daemon\.exe$') {
    throw 'Local port 502 is not the official AutoShop simulator; no request sent.'
}

function Read-Exact($stream,[int]$count) {
    $buffer = New-Object byte[] $count
    $offset = 0
    while($offset -lt $count) {
        $read = $stream.Read($buffer,$offset,$count-$offset)
        if(!$read) {throw 'Simulator closed the connection.'}
        $offset += $read
    }
    return ,$buffer
}

function Read-Holding([int]$start,[int]$count,[int]$transaction) {
    # Fixed loopback destination and FC03 only: writes are deliberately unsupported.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.ReceiveTimeout = 3000; $client.SendTimeout = 3000
        $client.Connect('127.0.0.1',502)
        $stream = $client.GetStream()
        [byte[]]$request = @(($transaction -shr 8),($transaction -band 255),0,0,0,6,1,3,
                           ($start -shr 8),($start -band 255),($count -shr 8),($count -band 255))
        $stream.Write($request,0,$request.Length)
        $header = Read-Exact $stream 7
        $length = ([int]$header[4] -shl 8) -bor $header[5]
        if($length -lt 3 -or $length -gt 254) {throw 'Invalid Modbus response length.'}
        $payload = Read-Exact $stream ($length-1)
        if($header[0] -ne $request[0] -or $header[1] -ne $request[1] -or $header[2] -ne 0 -or $header[3] -ne 0) {throw 'Modbus transaction/protocol mismatch.'}
        if($payload[0] -ne 3) {throw "Modbus exception: $($payload[1])"}
        if($payload[1] -ne 2*$count -or $payload.Length -ne 2+2*$count) {throw 'Register count mismatch.'}
        $values = @()
        for($i=0;$i -lt $count;$i++) {$values += (([int]$payload[2+2*$i] -shl 8) -bor $payload[3+2*$i])}
        return ,$values
    } finally {$client.Dispose()}
}

$summary = Read-Holding 500 24 1
$cases = Read-Holding 601 28 2
[ordered]@{
    captured_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
    destination = '127.0.0.1:502'
    simulator_path = $daemon.Path
    simulator_pid = $daemon.Id
    project = $root.Current.Name
    protocol = 'Modbus TCP FC03 read only; offset 500 maps to D500'
    summary_registers_D500_D523 = $summary
    case_registers_D601_D628 = $cases
    passed = $summary[0]
    failed = $summary[1]
    total = $summary[2]
    first_failed_case = $summary[3]
    completion_magic = $summary[4]
    executed_main_scans = $summary[6]
    first_failed_step_signed = $(if($summary[14] -ge 32768){$summary[14]-65536}else{$summary[14]})
    oracle_revision = $summary[23]
    accepted = ($summary[0] -eq 28 -and $summary[1] -eq 0 -and $summary[2] -eq 28 -and $summary[3] -eq 0 -and $summary[4] -eq 521 -and $summary[6] -eq 234 -and $summary[23] -eq 1 -and ($cases | Where-Object {$_ -ne 1}).Count -eq 0)
} | ConvertTo-Json -Depth 4

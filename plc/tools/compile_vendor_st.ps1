# Host-only, experimental STTemp -> ARM object worker. No device or AutoShop GUI APIs.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CompilerPath,
    [Parameter(Mandatory=$true)][string]$InputDirectory,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [ValidateRange(100,30000)][int]$TimeoutMs=4000
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$record=[ordered]@{schema='plcidf.vendor-object-build.v1';scope='vendor-object-only';ok=$false;status='failed';stage='preflight';diagnostics=@();build_directory=$BuildDirectory;source_files=@();object_files=@();device_access=$false;gui_access=$false}
$process=$null;$mapping=$null;$view=$null;$job=$null
try {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
public static class PlcIdfVendorHost {
 [DllImport("kernel32.dll")] public static extern uint SetErrorMode(uint mode);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool CreateDirectory(string path, IntPtr security);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr CreateJobObject(IntPtr security, string name);
 [DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetInformationJobObject(IntPtr job, int type, ref JobLimits value, uint length);
 [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
 [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
 [StructLayout(LayoutKind.Sequential)] public struct BasicLimits { public long PerProcess, PerJob; public uint Flags; public UIntPtr MinWorking, MaxWorking; public uint Active; public UIntPtr Affinity; public uint Priority, Scheduling; }
 [StructLayout(LayoutKind.Sequential)] public struct IoCounters { public ulong ReadOps,WriteOps,OtherOps,ReadBytes,WriteBytes,OtherBytes; }
 [StructLayout(LayoutKind.Sequential)] public struct JobLimits { public BasicLimits Basic; public IoCounters Io; public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob; }
 public sealed class Capture {
  readonly StringBuilder value=new StringBuilder(); public bool Truncated; public Task Completion;
  public string Text { get { lock(value) { return value.ToString(); } } }
  public Capture(StreamReader reader, int limit) { Completion=Task.Run(async () => {
   char[] buffer=new char[2048]; int count;
   while((count=await reader.ReadAsync(buffer,0,buffer.Length))>0) { lock(value) {
    int keep=Math.Min(count,Math.Max(0,limit-value.Length)); value.Append(buffer,0,keep); if(keep<count) Truncated=true;
   } }
  }); }
 }
}
'@
    [void][PlcIdfVendorHost]::SetErrorMode(0x8003)
    function Normalize-LocalPath([string]$Value) {
        if($Value -notmatch '^[A-Za-z]:\\' -or $Value -match '[^\x20-\x7e]' -or $Value.Length -gt 200) { throw 'Only explicit ASCII local-drive paths of at most 200 characters are supported' }
        $path=[IO.Path]::GetFullPath($Value).TrimEnd('\')
        if($path.Length -le 3 -or $path.Contains('"') -or $path.Substring(2).Contains(':')) { throw 'Invalid local path' }
        return $path
    }
    function Assert-NoReparse([string]$Value) {
        $current=$Value
        while($current -and $current.Length -gt 3) {
            if(Test-Path -LiteralPath $current) {
                if(((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse points are not supported' }
            }
            $current=[IO.Path]::GetDirectoryName($current)
        }
    }
    function Below([string]$Child,[string]$Parent) { return $Child.Equals($Parent,[StringComparison]::OrdinalIgnoreCase) -or $Child.StartsWith($Parent.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) }
    function Hash-File([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
    function File-Info([string]$Path) { return [ordered]@{name=[IO.Path]::GetFileName($Path);path=$Path;bytes=(Get-Item -LiteralPath $Path).Length;sha256=(Hash-File $Path)} }
    function Check-ArmObject([byte[]]$Data) {
        if($Data.Length -lt 52 -or [BitConverter]::ToString($Data[0..6]) -ne '7F-45-4C-46-01-01-01' -or [BitConverter]::ToUInt16($Data,16) -ne 1 -or [BitConverter]::ToUInt16($Data,18) -ne 40 -or [BitConverter]::ToUInt16($Data,40) -ne 52) { throw 'Object is not an ELF32 little-endian ARM relocatable' }
        [long]$table=[BitConverter]::ToUInt32($Data,32);[int]$count=[BitConverter]::ToUInt16($Data,48)
        if([BitConverter]::ToUInt16($Data,46) -ne 40 -or $count -lt 1 -or $count -gt 8192 -or $table -lt 52 -or $table+40L*$count -gt $Data.Length) { throw 'Invalid ELF section table' }
        $sections=@()
        for($i=0;$i -lt $count;$i++) {
            $offset=[int]($table+40L*$i)
            $section=@{type=[BitConverter]::ToUInt32($Data,$offset+4);offset=[BitConverter]::ToUInt32($Data,$offset+16);size=[BitConverter]::ToUInt32($Data,$offset+20);link=[BitConverter]::ToUInt32($Data,$offset+24);entry=[BitConverter]::ToUInt32($Data,$offset+36)}
            if($section.type -ne 8 -and [long]$section.offset+$section.size -gt $Data.Length) { throw 'Truncated ELF section' }
            $sections+=$section
        }
        $found=$false
        foreach($section in $sections) {
            if($section.type -ne 2) { continue }
            if($section.entry -ne 16 -or $section.size%16 -ne 0 -or $section.link -ge $count) { throw 'Invalid ELF symbol table' }
            $strings=$sections[$section.link]
            if($strings.type -ne 3) { throw 'Invalid ELF symbol strings' }
            for([long]$j=0;$j -lt $section.size;$j+=16) {
                $offset=[int]($section.offset+$j);[long]$name=[BitConverter]::ToUInt32($Data,$offset)
                if($name -ge $strings.size) { throw 'Invalid ELF symbol name' }
                [long]$start=$strings.offset+$name;[long]$end=$start
                while($end -lt [long]$strings.offset+$strings.size -and $Data[$end] -ne 0) { $end++ }
                if($end -eq [long]$strings.offset+$strings.size) { throw 'Unterminated ELF symbol name' }
                $symbol=[Text.Encoding]::ASCII.GetString($Data,[int]$start,[int]($end-$start))
                if($symbol -eq 'MAIN' -and ($Data[$offset+12] -band 15) -eq 2) {
                    $index=[BitConverter]::ToUInt16($Data,$offset+14);[long]$size=[BitConverter]::ToUInt32($Data,$offset+8);[long]$value=[BitConverter]::ToUInt32($Data,$offset+4) -band 0xfffffffeL
                    if($index -gt 0 -and $index -lt $count -and $size -gt 0 -and $value+$size -le $sections[$index].size) { $found=$true }
                }
            }
        }
        if(-not $found) { throw 'ARM object has no bounded defined MAIN function' }
        return @{class='ELF32';byte_order='little';machine='ARM';type='REL';entry_symbol='MAIN';eabi_flags=('0x{0:x8}' -f [BitConverter]::ToUInt32($Data,36))}
    }
    $CompilerPath=Normalize-LocalPath $CompilerPath;$InputDirectory=Normalize-LocalPath $InputDirectory;$BuildDirectory=Normalize-LocalPath $BuildDirectory
    $record.build_directory=$BuildDirectory
    $known=@{
        'E:\Software\AutoShop\Bin\STLang\StLang.exe'='b69439a1bb91f8dc6d982760023518671eff80e4cb0e90cc509c56e87e4f424a';
        'E:\Software\AutoShop\Bin\STLang_New\StLang.exe'='a9e2eee650729aa25c5b8f2fa1b595776c219d63c0b63f73420e59270ea54847'
    }
    if(-not $known.ContainsKey($CompilerPath)) { throw 'Compiler path is outside the two inspected local StLang installations' }
    Assert-NoReparse $CompilerPath;Assert-NoReparse $InputDirectory;Assert-NoReparse $BuildDirectory
    if(-not (Test-Path -LiteralPath $InputDirectory -PathType Container)) { throw 'Input directory does not exist' }
    if(Test-Path -LiteralPath $BuildDirectory) { throw 'Build directory must not already exist' }
    if((Below $BuildDirectory $InputDirectory) -or (Below $InputDirectory $BuildDirectory) -or (Below $BuildDirectory 'E:\Software\AutoShop') -or (Below $InputDirectory 'E:\Software\AutoShop')) { throw 'Input/build directories must be separate and outside the vendor installation' }
    $parent=[IO.Path]::GetDirectoryName($BuildDirectory)
    if(-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Build parent directory must already exist' }
    $kernel=Join-Path ([IO.Path]::GetDirectoryName($CompilerPath)) 'Kernel.dll';Assert-NoReparse $kernel
    $record.compiler=File-Info $CompilerPath;$record.kernel=File-Info $kernel
    if($record.compiler.sha256 -ne $known[$CompilerPath]) { throw 'Inspected compiler SHA256 does not match installed file' }
    $sources=@()
    foreach($name in @('MAIN.st','MAIN.cl')) {
        $path=Join-Path $InputDirectory $name;Assert-NoReparse $path
        if(-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw ('Required input is absent: '+$name) }
        $info=File-Info $path
        if($info.bytes -gt 33554432) { throw 'Input exceeds 32 MiB bound' }
        $sources+=$info
    }
    $inputData=[IO.File]::ReadAllBytes($sources[0].path)
    if($inputData.Length -lt 32 -or [Text.Encoding]::ASCII.GetString($inputData,0,12) -ne "****AS*****`0") { throw 'Only an existing vendor STTemp MAIN.st container is accepted; plain IEC text requires vendor preprocessing' }
    $record.source_files=$sources
    if(-not [PlcIdfVendorHost]::CreateDirectory($BuildDirectory,[IntPtr]::Zero)) { throw 'Cannot exclusively create fresh build directory' }
    $record.stage='copy'
    foreach($source in $sources) {
        $target=Join-Path $BuildDirectory $source.name
        [IO.File]::Copy($source.path,$target,$false)
        if((Hash-File $target) -ne $source.sha256 -or (Hash-File $source.path) -ne $source.sha256) { throw 'Input changed while copying' }
    }
    $stream=New-Object IO.MemoryStream;$writer=New-Object IO.BinaryWriter($stream)
    try {
        $cwd=[Text.Encoding]::ASCII.GetBytes($BuildDirectory);$writer.Write([uint32]$cwd.Length);$writer.Write($cwd);$writer.Write([uint32]0)
        $taskStream=New-Object IO.MemoryStream;$taskWriter=New-Object IO.BinaryWriter($taskStream)
        try {
            foreach($value in @('MAIN','ST','MAIN.st','MAIN.o')) { $taskWriter.Write([Text.Encoding]::ASCII.GetBytes($value));$taskWriter.Write([byte]0) }
            $taskWriter.Write([uint32]0);$taskWriter.Write([byte]1);$taskWriter.Write([byte]0);$taskWriter.Write([byte]0);$taskWriter.Write([byte]0);$taskWriter.Write([byte]0);$taskWriter.Flush();$taskBytes=$taskStream.ToArray()
        } finally { $taskWriter.Dispose();$taskStream.Dispose() }
        $writer.Write([uint32]$taskBytes.Length);$writer.Write([byte]0);$statusOffset=$stream.Position+$taskBytes.Length-3;$writer.Write($taskBytes);$writer.Flush();$payload=$stream.ToArray()
    } finally { $writer.Dispose();$stream.Dispose() }
    $mappingName='Local\plcidf_STLang_'+[guid]::NewGuid().ToString('N')
    $mapping=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew($mappingName,65536);$view=$mapping.CreateViewAccessor(0,65536);$view.WriteArray(0,$payload,0,$payload.Length);$view.Flush()
    $job=[PlcIdfVendorHost]::CreateJobObject([IntPtr]::Zero,$null)
    if($job -eq [IntPtr]::Zero) { throw 'Cannot create bounded compiler job' }
    $limits=New-Object PlcIdfVendorHost+JobLimits;$limits.Basic.Flags=0x2000
    if(-not [PlcIdfVendorHost]::SetInformationJobObject($job,9,[ref]$limits,[uint32][Runtime.InteropServices.Marshal]::SizeOf($limits))) { throw 'Cannot configure compiler cleanup job' }
    $info=New-Object Diagnostics.ProcessStartInfo;$info.FileName=$CompilerPath;$info.Arguments=$mappingName+' 0 0 0';$info.WorkingDirectory=$BuildDirectory;$info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.ErrorDialog=$false;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process;$process.StartInfo=$info;$record.stage='compile';$watch=[Diagnostics.Stopwatch]::StartNew();[void]$process.Start();$record.pid=$process.Id
    if(-not [PlcIdfVendorHost]::AssignProcessToJobObject($job,$process.Handle)) { throw 'Cannot attach own compiler to cleanup job' }
    $stdout=New-Object PlcIdfVendorHost+Capture($process.StandardOutput,131072);$stderr=New-Object PlcIdfVendorHost+Capture($process.StandardError,131072)
    $exited=$process.WaitForExit($TimeoutMs);$record.timed_out=(-not $exited)
    if(-not $exited) { $process.Kill();[void]$process.WaitForExit(2000) }
    [void]$stdout.Completion.Wait(1000);[void]$stderr.Completion.Wait(1000)
    $record.elapsed_ms=$watch.ElapsedMilliseconds;$record.exit_code=$process.ExitCode;$record.stdout=$stdout.Text;$record.stderr=$stderr.Text;$record.output_truncated=($stdout.Truncated -or $stderr.Truncated);$record.capture_complete=($stdout.Completion.IsCompleted -and $stderr.Completion.IsCompleted)
    $record.ipc=@{started=$view.ReadByte($statusOffset);done=$view.ReadByte($statusOffset+1);success=$view.ReadByte($statusOffset+2)}
    $record.stage='validate'
    if($record.timed_out) { throw 'Compiler timed out; own compiler process was terminated' }
    if(-not $record.capture_complete -or $record.output_truncated) { throw 'Compiler diagnostics were incomplete or exceeded capture bound' }
    if($record.stdout -match '(?i)\b(error|fatal|failed|failure|exception|lexer|syntax|unresolved|missing)\b|not exist' -or $record.stderr.Length -ne 0) { throw 'Compiler emitted error diagnostics, regardless of later success markers' }
    if($record.exit_code -ne 1 -or $record.ipc.started -ne 1 -or $record.ipc.done -ne 1 -or $record.ipc.success -ne 1) { throw 'Compiler did not report successful completion of this mapped task' }
    $rstPath=Join-Path $BuildDirectory 'MAIN.rst';Assert-NoReparse $rstPath
    if(-not (Test-Path -LiteralPath $rstPath -PathType Leaf) -or (Get-Item -LiteralPath $rstPath).Length -ne 10 -or [Convert]::ToBase64String([IO.File]::ReadAllBytes($rstPath)) -ne 'U3VjY2VzcyENCg==') { throw 'Fresh result file is not exact Success CRLF' }
    $objectPath=Join-Path $BuildDirectory 'MAIN.o';Assert-NoReparse $objectPath
    if(-not (Test-Path -LiteralPath $objectPath -PathType Leaf) -or (Get-Item -LiteralPath $objectPath).Length -gt 33554432) { throw 'Fresh bounded MAIN.o is absent' }
    $objectData=[IO.File]::ReadAllBytes($objectPath);$elf=Check-ArmObject $objectData;$object=File-Info $objectPath;$object.elf=$elf;$record.object_files=@($object);$record.result_file=File-Info $rstPath
    foreach($source in $sources) {
        if((Hash-File $source.path) -ne $source.sha256 -or (Hash-File (Join-Path $BuildDirectory $source.name)) -ne $source.sha256) { throw 'Source changed during compile' }
    }
    if((Hash-File $CompilerPath) -ne $record.compiler.sha256 -or (Hash-File $kernel) -ne $record.kernel.sha256) { throw 'Compiler environment changed during compile' }
    $record.ok=$true;$record.status='verified';$record.stage='complete'
} catch {
    $record.ok=$false;$record.status='failed';$record.diagnostics+=@{code='E_VENDOR_OBJECT';stage=$record.stage;message=$_.Exception.Message}
} finally {
    if($process) {
        try { if(-not $process.HasExited) { $process.Kill();[void]$process.WaitForExit(2000) } } catch { $record.ok=$false;$record.status='failed';$record.diagnostics+=@{code='E_CLEANUP';stage='cleanup';message=$_.Exception.Message} }
        $process.Dispose()
    }
    if($job -and $job -ne [IntPtr]::Zero) { [void][PlcIdfVendorHost]::CloseHandle($job) }
    if($view) { $view.Dispose() };if($mapping) { $mapping.Dispose() }
}
$record|ConvertTo-Json -Depth 12 -Compress
if($record.ok) { exit 0 } else { exit 1 }

param(
    [ValidateSet('inspect','status','dialog','button','check','close-dialog','screenshot','desktop','tree','keys','click','paste','background','menus','text','file','accept','log','log-data','diagnostics','maximize','editor-read','editor-set')]
    [string]$Action = 'inspect',
    [string]$Value = '',
    [int]$X = 0,
    [int]$Y = 0,
    [int]$Handle = 0,
    [int]$Message = 0,
    [string]$ExpectedProject = '',
    [string]$ExpectedDialog = '',
    [string]$ExpectedText = '',
    [ValidateSet(-1,0,1)][int]$CheckState = -1,
    [switch]$Right,
    [switch]$Double
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Pure title validation is kept separate from UI access so it can be exercised
# without starting AutoShop or sending any window messages.
function Assert-AutoShopTitle {
    param([string]$Title, [string]$ExpectedProject,
          [switch]$RequireMain, [switch]$RequireClean)
    if(!$ExpectedProject){throw 'An exact expected project path is required.'}
    $match=[regex]::Match($Title, '^AutoShop\s+V\d+(?:\.\d+){1,3}\s+(?<project>[A-Za-z]:[\\/].+?)\s+-\s+\[(?<document>[^\]]+)\]$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if(!$match.Success){throw 'AutoShop project/document title is not recognized.'}
    if($ExpectedProject -notmatch '^[A-Za-z]:[\\/]'){throw 'An absolute Windows project path is required.'}
    $actualPath=[System.IO.Path]::GetFullPath($match.Groups['project'].Value).TrimEnd('\','/')
    $expectedPath=[System.IO.Path]::GetFullPath($ExpectedProject).TrimEnd('\','/')
    if(![string]::Equals($actualPath,$expectedPath,[System.StringComparison]::OrdinalIgnoreCase)){
        throw 'Exact AutoShop project mismatch; no input sent.'
    }
    $document=$match.Groups['document'].Value.Trim()
    if($RequireMain -and $document -notmatch '^MAIN(?:\s*\*)?$'){
        throw 'The current AutoShop document must be MAIN; no editor text written.'
    }
    if($RequireClean -and $document.Contains('*')){
        throw 'AutoShop has unsaved editor changes; no editor text written.'
    }
}

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class AutoShopUI {
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetDpiForSystem();
 [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h,int index);
 [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT rect);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out RECT rect);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetLastActivePopup(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder text,int count);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a,uint b,bool attach);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint msg,IntPtr wp,IntPtr lp);
 [DllImport("user32.dll")] public static extern IntPtr GetMenu(IntPtr h);
 [DllImport("user32.dll")] public static extern int GetMenuItemCount(IntPtr menu);
 [DllImport("user32.dll")] public static extern IntPtr GetSubMenu(IntPtr menu,int pos);
 [DllImport("user32.dll")] public static extern uint GetMenuItemID(IntPtr menu,int pos);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetMenuString(IntPtr menu,uint item,StringBuilder text,int count,uint flags);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,uint msg,IntPtr wp,IntPtr lp);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h,uint msg,IntPtr wp,string lp);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h,uint msg,IntPtr wp,StringBuilder lp);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll")] static extern IntPtr VirtualAllocEx(IntPtr process,IntPtr addr,UIntPtr size,uint type,uint protect);
 [DllImport("kernel32.dll")] static extern bool VirtualFreeEx(IntPtr process,IntPtr addr,UIntPtr size,uint type);
 [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr process,IntPtr addr,byte[] buffer,int count,out IntPtr written);
 [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr process,IntPtr addr,byte[] buffer,int count,out IntPtr read);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 public static void Click(int x,int y,bool right,bool twice) {
   SetCursorPos(x,y);uint down=right?8u:2u,up=right?16u:4u;
   mouse_event(down,0,0,0,UIntPtr.Zero);mouse_event(up,0,0,0,UIntPtr.Zero);
   if(twice){System.Threading.Thread.Sleep(120);mouse_event(down,0,0,0,UIntPtr.Zero);mouse_event(up,0,0,0,UIntPtr.Zero);}
 }
 // AutoShop is a 32-bit process; TVITEM fields must use its 32-bit layout.
 static string TreeText(IntPtr tree,IntPtr item,IntPtr process) {
   IntPtr remote=VirtualAllocEx(process,IntPtr.Zero,(UIntPtr)1064,0x3000,4);
   if(remote==IntPtr.Zero)throw new InvalidOperationException("VirtualAllocEx failed");
   try {
     byte[] b=new byte[40];Array.Copy(BitConverter.GetBytes(1u),0,b,0,4);
     Array.Copy(BitConverter.GetBytes((uint)item.ToInt64()),0,b,4,4);
     Array.Copy(BitConverter.GetBytes((uint)(remote.ToInt64()+40)),0,b,16,4);
     Array.Copy(BitConverter.GetBytes(512),0,b,20,4);
     IntPtr n;WriteProcessMemory(process,remote,b,b.Length,out n);
     SendMessage(tree,0x113E,IntPtr.Zero,remote);
     byte[] t=new byte[1024];ReadProcessMemory(process,IntPtr.Add(remote,40),t,t.Length,out n);
     return Encoding.Unicode.GetString(t).TrimEnd((char)0);
   } finally {VirtualFreeEx(process,remote,UIntPtr.Zero,0x8000);}
 }
 static void Walk(IntPtr tree,IntPtr item,IntPtr process,int depth,List<string> result) {
   while(item!=IntPtr.Zero){result.Add(new string((char)32,depth*2)+item.ToInt64()+"|"+TreeText(tree,item,process));
     IntPtr child=SendMessage(tree,0x110A,(IntPtr)4,item);if(child!=IntPtr.Zero)Walk(tree,child,process,depth+1,result);
     item=SendMessage(tree,0x110A,(IntPtr)1,item);}
 }
 public static string[] Tree(IntPtr tree) {
   uint pid;GetWindowThreadProcessId(tree,out pid);IntPtr process=OpenProcess(0x38,false,pid);
   if(process==IntPtr.Zero)throw new InvalidOperationException("Cannot read AutoShop tree");
   try{var result=new List<string>();Walk(tree,SendMessage(tree,0x110A,IntPtr.Zero,IntPtr.Zero),process,0,result);return result.ToArray();}
   finally{CloseHandle(process);}
 }
 public static string EditorText(IntPtr editor, string replacement, bool replace) {
   uint pid;GetWindowThreadProcessId(editor,out pid);IntPtr process=OpenProcess(0x38,false,pid);
   if(process==IntPtr.Zero)throw new InvalidOperationException("Cannot access AutoShop editor");
   IntPtr remote=IntPtr.Zero;
   try {
     int page=SendMessage(editor,2137,IntPtr.Zero,IntPtr.Zero).ToInt32();
     Encoding encoding=page==65001?new UTF8Encoding(false,true):Encoding.GetEncoding(page==0?936:page);
     if(replace) {
       if(SendMessage(editor,2140,IntPtr.Zero,IntPtr.Zero).ToInt32()!=0)
         throw new InvalidOperationException("AutoShop ST editor is read-only; exit online monitoring before editing.");
       byte[] data=encoding.GetBytes(replacement+"\0");
       if(data.Length>1048576)throw new InvalidOperationException("Editor replacement exceeds 1MiB");
       remote=VirtualAllocEx(process,IntPtr.Zero,(UIntPtr)data.Length,0x3000,4);
       if(remote==IntPtr.Zero)throw new InvalidOperationException("Editor allocation failed");
       IntPtr written;if(!WriteProcessMemory(process,remote,data,data.Length,out written)||written.ToInt64()!=data.Length)throw new InvalidOperationException("Editor write failed");
       SendMessage(editor,2181,IntPtr.Zero,remote);
       VirtualFreeEx(process,remote,UIntPtr.Zero,0x8000);remote=IntPtr.Zero;
     }
     int length=SendMessage(editor,2183,IntPtr.Zero,IntPtr.Zero).ToInt32();
     if(length<0||length>1048576)throw new InvalidOperationException("Invalid editor length");
     remote=VirtualAllocEx(process,IntPtr.Zero,(UIntPtr)(length+1),0x3000,4);
     if(remote==IntPtr.Zero)throw new InvalidOperationException("Editor allocation failed");
     SendMessage(editor,2182,(IntPtr)(length+1),remote);
     byte[] text=new byte[length];IntPtr read;
     if(!ReadProcessMemory(process,remote,text,length,out read)||read.ToInt64()!=length)throw new InvalidOperationException("Editor read failed");
     return encoding.GetString(text);
   } finally {if(remote!=IntPtr.Zero)VirtualFreeEx(process,remote,UIntPtr.Zero,0x8000);CloseHandle(process);}
 }
 public static string[] LogData(IntPtr list) {
   // AutoShop 4.12.0.2 stores UTF-16 row strings in owner-drawn item data.
   // Read only that control's bounded item buffers; never follow arbitrary fields.
   uint pid;GetWindowThreadProcessId(list,out pid);IntPtr process=OpenProcess(0x10,false,pid);
   if(process==IntPtr.Zero)throw new InvalidOperationException("Cannot read AutoShop log");
   try {
     var rows=new List<string>();int count=SendMessage(list,0x18B,IntPtr.Zero,IntPtr.Zero).ToInt32();
     if(count<0||count>10000)throw new InvalidOperationException("Invalid log item count");
     for(int i=0;i<count;i++) {
       IntPtr item=SendMessage(list,0x199,(IntPtr)i,IntPtr.Zero);
       if(item.ToInt64()<65536)throw new InvalidOperationException("Invalid log text buffer");
       var bytes=new List<byte>();bool terminated=false;
       for(int offset=0;offset<8192&&!terminated;offset+=2) {
         byte[] pair=new byte[2];IntPtr read;
         if(!ReadProcessMemory(process,IntPtr.Add(item,offset),pair,2,out read)||read.ToInt64()!=2)throw new InvalidOperationException("Log text is unreadable");
         if(pair[0]==0&&pair[1]==0){terminated=true;break;}
         bytes.AddRange(pair);
       }
       if(!terminated)throw new InvalidOperationException("Log text exceeds bounded buffer");
       string row=new UnicodeEncoding(false,false,true).GetString(bytes.ToArray());
       foreach(char ch in row)if(Char.IsControl(ch)&&ch!='\t'&&ch!='\r'&&ch!='\n')throw new InvalidOperationException("Invalid log text characters");
       rows.Add(row);
     }
     if(SendMessage(list,0x18B,IntPtr.Zero,IntPtr.Zero).ToInt32()!=count)throw new InvalidOperationException("Log changed while reading");
     return rows.ToArray();
   } finally {CloseHandle(process);}
 }

}
'@
[void][AutoShopUI]::SetProcessDPIAware()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$app = Get-Process AutoShop | Select-Object -First 1
$topWindows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
$root=$topWindows|Where-Object {$_.Current.ProcessId -eq $app.Id -and $_.Current.Name.StartsWith('AutoShop ') -and $_.Current.ClassName.StartsWith('Afx:')}|Select-Object -First 1
if(!$root){throw 'AutoShop main frame not found.'}
$mainFrameHandle=[IntPtr]$root.Current.NativeWindowHandle
function Get-AutoShopTitle {
    $titleBuffer=New-Object System.Text.StringBuilder 4096
    if([AutoShopUI]::GetWindowText($mainFrameHandle,$titleBuffer,$titleBuffer.Capacity) -le 0){
        throw 'Cannot read current AutoShop title; no input sent.'
    }
    return $titleBuffer.ToString()
}
function Get-ExactDialog {
    if(!$ExpectedDialog){throw 'An exact expected dialog title is required.'}
    $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))
    $windows+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
    $dialogs=@($windows|Where-Object {$_.Current.ProcessId -eq $app.Id -and $_.Current.ClassName -eq '#32770' -and $_.Current.Name -ceq $ExpectedDialog -and $_.Current.IsEnabled -and !$_.Current.IsOffscreen}|Sort-Object {$_.Current.NativeWindowHandle} -Unique)
    if($dialogs.Count -ne 1){throw 'Expected one exact active AutoShop dialog; no input sent.'}
    if($ExpectedProject){Assert-AutoShopTitle -Title (Get-AutoShopTitle) -ExpectedProject $ExpectedProject}
    $controls=@($dialogs[0].FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
    if($ExpectedText -and !($controls|Where-Object {$_.Current.ClassName -eq 'Static' -and $_.Current.Name.Contains($ExpectedText)})){
        throw 'Expected dialog message is absent; no input sent.'
    }
    return $dialogs[0]
}
if($ExpectedProject -and $Action -in @('keys','editor-read','editor-set','log','log-data')){
    Assert-AutoShopTitle -Title (Get-AutoShopTitle) -ExpectedProject $ExpectedProject
}

function Activate-AutoShop {
    if([AutoShopUI]::IsIconic($mainFrameHandle)){[void][AutoShopUI]::ShowWindow($mainFrameHandle,9)}
    $activationHandle=$mainFrameHandle
    if(![AutoShopUI]::IsWindowEnabled($mainFrameHandle)){
        $popupHandle=[AutoShopUI]::GetLastActivePopup($mainFrameHandle)
        if($popupHandle -eq $mainFrameHandle -or ![AutoShopUI]::IsWindowEnabled($popupHandle)){
            $possibleWindows=@($topWindows)
            $possibleWindows+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty),([System.Windows.Automation.ControlType]::Window))))
            $modalCandidates=@($possibleWindows|Where-Object {$_.Current.ProcessId -eq $app.Id -and $_.Current.IsEnabled -and $_.Current.ClassName -eq '#32770'}|Sort-Object {$_.Current.NativeWindowHandle} -Unique)
            if($modalCandidates.Count -ne 1){throw 'Expected one AutoShop modal window; no input was sent.'}
            $popupHandle=[IntPtr]$modalCandidates[0].Current.NativeWindowHandle
        }
        [uint32]$popupPid=0
        [void][AutoShopUI]::GetWindowThreadProcessId($popupHandle,[ref]$popupPid)
        if($popupPid -ne $app.Id -or ![AutoShopUI]::IsWindowEnabled($popupHandle) -or ![AutoShopUI]::IsWindowVisible($popupHandle)){
            throw 'AutoShop modal window could not be safely activated; no input was sent.'
        }
        $activationHandle=$popupHandle
    }
    $activationShell=New-Object -ComObject WScript.Shell
    [void]$activationShell.AppActivate($app.Id)
    [uint32]$foregroundPid=0
    $foregroundThread=[AutoShopUI]::GetWindowThreadProcessId([AutoShopUI]::GetForegroundWindow(),[ref]$foregroundPid)
    $currentThread=[AutoShopUI]::GetCurrentThreadId()
    $attached=[AutoShopUI]::AttachThreadInput($currentThread,$foregroundThread,$true)
    try{[void][AutoShopUI]::BringWindowToTop($activationHandle)
    [void][AutoShopUI]::SetForegroundWindow($activationHandle)
    }finally{if($attached){[void][AutoShopUI]::AttachThreadInput($currentThread,$foregroundThread,$false)}}
    Start-Sleep -Milliseconds 500
    [uint32]$foregroundPid=0
    [void][AutoShopUI]::GetWindowThreadProcessId([AutoShopUI]::GetForegroundWindow(),[ref]$foregroundPid)
    if($foregroundPid -ne $app.Id){throw 'AutoShop is not foreground; no input was sent.'}
}

switch ($Action) {
    'close-dialog' {
        $dialog=Get-ExactDialog
        [void][AutoShopUI]::PostMessage([IntPtr]$dialog.Current.NativeWindowHandle,16,[IntPtr]::Zero,[IntPtr]::Zero)
        @{dialog=$ExpectedDialog;close_posted=$true}|ConvertTo-Json -Compress
    }
    'dialog' {
        $dialog=Get-ExactDialog
        $controls=@($dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|ForEach-Object {
            $checked=$null;$style=$null
            if($_.Current.ClassName -eq 'Button'){
                $style=[AutoShopUI]::GetWindowLong([IntPtr]$_.Current.NativeWindowHandle,-16) -band 15
                $checked=[AutoShopUI]::SendMessage([IntPtr]$_.Current.NativeWindowHandle,240,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
            }
            @{name=$_.Current.Name;class=$_.Current.ClassName;handle=$_.Current.NativeWindowHandle;enabled=$_.Current.IsEnabled;checked=$checked;button_type=$style}
        })
        @{title=$dialog.Current.Name;handle=$dialog.Current.NativeWindowHandle;project=(Get-AutoShopTitle);controls=$controls}|ConvertTo-Json -Depth 5 -Compress
    }
    {$_ -in @('button','check')} {
        $dialog=Get-ExactDialog
        if(!$Value){throw 'An exact control name is required.'}
        $buttons=@($dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ClassName -eq 'Button' -and $_.Current.Name -ceq $Value -and $_.Current.IsEnabled})
        if($buttons.Count -ne 1){throw 'Expected one exact enabled dialog button; no input sent.'}
        $buttonHandle=[IntPtr]$buttons[0].Current.NativeWindowHandle
        $kind=[AutoShopUI]::GetWindowLong($buttonHandle,-16) -band 15
        if($Action -eq 'check'){
            if($CheckState -eq -1 -or $kind -notin @(2,3,5,6)){throw 'An explicit check state and a checkbox control are required.'}
            $before=[AutoShopUI]::SendMessage($buttonHandle,240,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
            if($before -ne $CheckState){[void][AutoShopUI]::SendMessage($buttonHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)}
            $after=[AutoShopUI]::SendMessage($buttonHandle,240,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
            if($after -ne $CheckState){throw 'Checkbox readback differs; do not repeat the operation.'}
            @{dialog=$ExpectedDialog;button=$Value;before=$before;after=$after}|ConvertTo-Json -Compress
        }else{
            if($kind -notin @(0,1)){throw 'The requested control is not a push button.'}
            [void][AutoShopUI]::PostMessage($buttonHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)
            @{dialog=$ExpectedDialog;button=$Value;invocation_posted=$true;outcome_verified=$false}|ConvertTo-Json -Compress
        }
    }
    'status' {
        $all=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
        $items=@($all|Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::StatusBar -or $_.Current.ClassName -match 'status|Static|VSFlex|Button'}|ForEach-Object {
            @{name=$_.Current.Name;class=$_.Current.ClassName;handle=$_.Current.NativeWindowHandle;enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;type=$_.Current.ControlType.ProgrammaticName}
        })
        @{title=(Get-AutoShopTitle);process_id=$app.Id;controls=$items}|ConvertTo-Json -Depth 5 -Compress
    }
    'log-data' {
        if(!$root.Current.Name.StartsWith('AutoShop V4.12.0.2 ')){throw 'Owner-drawn log adapter is calibrated only for AutoShop 4.12.0.2.'}
        $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $list=$all|Where-Object {$_.Current.ClassName -eq 'ListBox' -and $_.Current.BoundingRectangle.Width -gt 1000}|Select-Object -First 1
        if(!$list){throw 'AutoShop compile log not found.'}
        [AutoShopUI]::LogData([IntPtr]$list.Current.NativeWindowHandle)
    }
    {$_ -in 'editor-read','editor-set'} {
        if($Action -eq 'editor-set' -and !$ExpectedProject){throw 'An exact expected project path is required for editor-set.'}
        $editors=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)|Where-Object {$_.Current.ClassName -eq 'Scintilla' -and $_.Current.IsEnabled -and !($_.Current.IsOffscreen)})
        if($editors.Count -ne 1){throw 'Expected exactly one visible AutoShop ST editor.'}
        $replacement=$null
        if($Action -eq 'editor-set'){
            if(!(Test-Path -LiteralPath $Value -PathType Leaf)){throw 'Editor source file is required.'}
            $replacement=[System.IO.File]::ReadAllText($Value)
            # Re-read immediately before replacement. The user may have changed
            # documents or made an unsaved edit since the previous Python call.
            if(!$editors[0].Current.IsEnabled -or $editors[0].Current.IsOffscreen){
                throw 'The selected AutoShop editor changed or became hidden; no text written.'
            }
            Assert-AutoShopTitle -Title (Get-AutoShopTitle) -ExpectedProject $ExpectedProject -RequireMain -RequireClean
        }
        $source=[AutoShopUI]::EditorText([IntPtr]$editors[0].Current.NativeWindowHandle,$replacement,($Action -eq 'editor-set'))
        @{title=(Get-AutoShopTitle);source=$source;editor_handle=$editors[0].Current.NativeWindowHandle;read_only=([AutoShopUI]::SendMessage([IntPtr]$editors[0].Current.NativeWindowHandle,2140,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32() -ne 0)}|ConvertTo-Json -Depth 3 -Compress
    }
    'inspect' {
        Write-Output ("PID="+$app.Id+"|"+$root.Current.Name)
        $windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
        $all=@()
        foreach($window in $windows){if($window.Current.ProcessId -eq $app.Id){$all+=$window;$all+=@($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))}}
        foreach($e in $all){
            $c=$e.Current.ClassName;$t=$e.Current.ControlType.ProgrammaticName;$r=$e.Current.BoundingRectangle
            if($t -eq 'ControlType.Window' -or $c -match 'Button|Edit|ComboBox|Scintilla|Tree|Grid|List|VSFlex' -or ($e.Current.Name -and $c -eq 'Static')){
                $controlName=if($c -eq 'Scintilla'){'<ST editor>'}else{$e.Current.Name}
                Write-Output ("{0}|{1}|{2}|{3}|{4}|{5},{6},{7},{8}" -f $t,$controlName,$c,$e.Current.NativeWindowHandle,$e.Current.IsEnabled,$r.X,$r.Y,$r.Width,$r.Height)
            }
        }
    }
    'screenshot' {
        if(!$Value){$Value='C:\Users\12646\Downloads\autoshop_latest.png'}
        $captureHandle=$mainFrameHandle
        if($Handle){
            [uint32]$capturePid=0
            [void][AutoShopUI]::GetWindowThreadProcessId([IntPtr]$Handle,[ref]$capturePid)
            if($capturePid -ne $app.Id){throw 'Screenshot target is not owned by AutoShop.'}
            $captureHandle=[IntPtr]$Handle
        }
        $rect=New-Object AutoShopUI+RECT
        $oldDpiContext=[AutoShopUI]::SetThreadDpiAwarenessContext([IntPtr](-1))
        try{[void][AutoShopUI]::GetWindowRect($captureHandle,[ref]$rect)}finally{[void][AutoShopUI]::SetThreadDpiAwarenessContext($oldDpiContext)}
        $w=$rect.Right-$rect.Left;$h=$rect.Bottom-$rect.Top
        $bmp=New-Object System.Drawing.Bitmap $w,$h;$g=[System.Drawing.Graphics]::FromImage($bmp)
        try{$dc=$g.GetHdc();try{[void][AutoShopUI]::PrintWindow($captureHandle,$dc,2)}finally{$g.ReleaseHdc($dc)};$bmp.Save($Value,[System.Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$bmp.Dispose()}
        Write-Output $Value
    }
    'desktop' {
        [uint32]$foregroundPid=0
        [void][AutoShopUI]::GetWindowThreadProcessId([AutoShopUI]::GetForegroundWindow(),[ref]$foregroundPid)
        if($foregroundPid -ne $app.Id){throw 'AutoShop is not foreground; desktop capture skipped.'}
        if(!$Value){$Value='C:\Users\12646\Downloads\autoshop_desktop.png'}
        $bmp=New-Object System.Drawing.Bitmap ([AutoShopUI]::GetSystemMetrics(0)),([AutoShopUI]::GetSystemMetrics(1))
        $g=[System.Drawing.Graphics]::FromImage($bmp)
        try{$g.CopyFromScreen(0,0,0,0,$bmp.Size);$bmp.Save($Value,[System.Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$bmp.Dispose()}
        Write-Output $Value
    }
    'tree' {
        $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $tree=$all|Where-Object {$_.Current.ClassName -eq 'SysTreeView32'}|Sort-Object {$_.Current.BoundingRectangle.X}|Select-Object -First 1
        Write-Output ("TREE="+$tree.Current.NativeWindowHandle)
        [AutoShopUI]::Tree([IntPtr]$tree.Current.NativeWindowHandle)
    }
    'log' {
        $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $list=$all|Where-Object {$_.Current.ClassName -eq 'ListBox' -and $_.Current.BoundingRectangle.Width -gt 1000}|Select-Object -First 1
        if(!$list){throw 'AutoShop compile log not found.'}
        $listHandle=[IntPtr]$list.Current.NativeWindowHandle
        if(([AutoShopUI]::GetWindowLong($listHandle,-16) -band 0x40) -ne 0){
            $count=[AutoShopUI]::SendMessage($listHandle,0x18B,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
            for($i=0;$i -lt $count;$i++){
                $line=New-Object System.Text.StringBuilder 16384
                [void][AutoShopUI]::SendMessage($listHandle,0x189,[IntPtr]$i,$line)
                Write-Output $line.ToString()
            }
        } else {
            if($root.Current.Name.StartsWith('AutoShop V4.12.0.2 ')){
                try {
                    $rows=[AutoShopUI]::LogData($listHandle)
                    if($rows.Count -gt 0){$rows;return}
                } catch {Write-Verbose ('Exact log read unavailable: '+$_.Exception.Message)}
            }
            $rect=New-Object AutoShopUI+RECT
            $oldDpi=[AutoShopUI]::SetThreadDpiAwarenessContext([IntPtr](-4))
            try{[void][AutoShopUI]::GetWindowRect($listHandle,[ref]$rect)}finally{[void][AutoShopUI]::SetThreadDpiAwarenessContext($oldDpi)}
            $w=$rect.Right-$rect.Left;$h=$rect.Bottom-$rect.Top
            $bmp=New-Object System.Drawing.Bitmap $w,$h;$g=[System.Drawing.Graphics]::FromImage($bmp)
            try{$dc=$g.GetHdc();try{[void][AutoShopUI]::PrintWindow($listHandle,$dc,2)}finally{$g.ReleaseHdc($dc)};$shot=if($Value){$Value}else{Join-Path $env:TEMP 'plcidf-autoshop-logpane.png'};$bmp.Save($shot,[System.Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$bmp.Dispose()}
            Write-Output ('SCREENSHOT:' + $shot)
        }
    }
    'diagnostics' {
        [uint32]$foregroundPid=0
        $foreground=[AutoShopUI]::GetForegroundWindow()
        [void][AutoShopUI]::GetWindowThreadProcessId($foreground,[ref]$foregroundPid)
        Write-Output ("AppPID={0} Session={1} Foreground={2} ForegroundPID={3} AppDPI={4} SystemDPI={5} Screen={6}x{7}" -f $app.Id,$app.SessionId,$foreground,$foregroundPid,[AutoShopUI]::GetDpiForWindow($mainFrameHandle),[AutoShopUI]::GetDpiForSystem(),[AutoShopUI]::GetSystemMetrics(0),[AutoShopUI]::GetSystemMetrics(1))
    }
    'maximize' {
        Activate-AutoShop
        [void][AutoShopUI]::ShowWindow($mainFrameHandle,3)
    }
    'keys' {
        Activate-AutoShop
        if($ExpectedProject){Assert-AutoShopTitle -Title (Get-AutoShopTitle) -ExpectedProject $ExpectedProject}
        $ws=New-Object -ComObject WScript.Shell;$ws.SendKeys($Value)
    }
    'click' {
        Activate-AutoShop
        [AutoShopUI]::Click($X,$Y,$Right.IsPresent,$Double.IsPresent)
    }
    'paste' {
        Activate-AutoShop
        $pasteText=if(Test-Path -LiteralPath $Value){[System.IO.File]::ReadAllText($Value)}else{$Value}
        [System.Windows.Forms.Clipboard]::SetText($pasteText)
        $ws=New-Object -ComObject WScript.Shell;$ws.SendKeys('^v')
    }
    'background' {
        if(!$Handle){throw 'An exact control handle is required.'}
        if($Message){[void][AutoShopUI]::PostMessage([IntPtr]$Handle,[uint32]$Message,[IntPtr]$X,[IntPtr]$Y)}
        else {
            $pt=[IntPtr](($Y -shl 16) -bor ($X -band 65535))
            if($Right){[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x204,[IntPtr]2,$pt);[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x205,[IntPtr]0,$pt)}
            else {[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x201,[IntPtr]1,$pt);[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x202,[IntPtr]0,$pt);if($Double){[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x203,[IntPtr]1,$pt);[void][AutoShopUI]::PostMessage([IntPtr]$Handle,0x202,[IntPtr]0,$pt)}}
        }
        Start-Sleep -Milliseconds 350
    }
    'text' {
        if(!$Handle){throw 'An exact edit handle is required.'}
        [void][AutoShopUI]::SendMessage([IntPtr]$Handle,12,[IntPtr]::Zero,$Value)
    }
    'file' {
        $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))
        $windows+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty),([System.Windows.Automation.ControlType]::Window))))
        $dialog=$null
        for($i=0;$i -lt 10 -and -not $dialog;$i++){
            $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))
            $windows+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty),([System.Windows.Automation.ControlType]::Window))))
            $dialog=$windows|Where-Object {$_.Current.ProcessId -eq $app.Id -and $_.Current.ClassName -eq '#32770' -and $_.Current.IsEnabled -and $_.Current.Name -match '^(另存为|打开|导入|导出)$'}|Select-Object -First 1
            if(-not $dialog){Start-Sleep -Milliseconds 1000}
        }
        if(!$dialog){throw 'No AutoShop file dialog found.'}
        $controls=$dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $edit=$controls|Where-Object {$_.Current.ClassName -eq 'Edit' -and $_.Current.BoundingRectangle.Width -gt 200}|Select-Object -First 1
        $button=$controls|Where-Object {$_.Current.ClassName -eq 'Button' -and $_.Current.Name -match '^(保存|打开|导入)'}|Select-Object -First 1
        if(!$edit -or !$button){throw 'File dialog controls not found.'}
        [void][AutoShopUI]::SendMessage([IntPtr]$edit.Current.NativeWindowHandle,12,[IntPtr]::Zero,$Value)
        [void][AutoShopUI]::PostMessage([IntPtr]$button.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)
        Start-Sleep -Milliseconds 500
    }
    'accept' {
        if(!$Value){throw 'Expected success-message text is required.'}
        $windows=@([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))
        $windows+=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty),([System.Windows.Automation.ControlType]::Window))))
        foreach($dialog in $windows){if($dialog.Current.ProcessId -eq $app.Id -and $dialog.Current.ClassName -eq '#32770' -and $dialog.Current.IsEnabled){
            $controls=$dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
            $successMessage=$controls|Where-Object {$_.Current.ClassName -eq 'Static' -and $_.Current.Name.Contains($Value)}|Select-Object -First 1
            if($successMessage){$button=$controls|Where-Object {$_.Current.ClassName -eq 'Button' -and $_.Current.Name -eq '确定'}|Select-Object -First 1
                if($button){Write-Output $successMessage.Current.Name;[void][AutoShopUI]::PostMessage([IntPtr]$button.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero);Start-Sleep -Milliseconds 300;return}
            }
        }}
        throw 'Expected success dialog was not found.'
    }
    'menus' {
        function Read-Menu([IntPtr]$menu,[string]$path){
            for($i=0;$i -lt [AutoShopUI]::GetMenuItemCount($menu);$i++){
                $text=New-Object System.Text.StringBuilder 512
                [void][AutoShopUI]::GetMenuString($menu,[uint32]$i,$text,512,1024)
                $name=$text.ToString();$id=[AutoShopUI]::GetMenuItemID($menu,$i)
                Write-Output ("$path/$name|$id")
                $sub=[AutoShopUI]::GetSubMenu($menu,$i);if($sub -ne [IntPtr]::Zero){Read-Menu $sub "$path/$name"}
            }
        }
        $h=if($Handle){[IntPtr]$Handle}else{$mainFrameHandle}
        $menu=[AutoShopUI]::GetMenu($h)
        if($menu -eq [IntPtr]::Zero){$menu=[AutoShopUI]::SendMessage($h,0x1E1,[IntPtr]::Zero,[IntPtr]::Zero)}
        Read-Menu $menu ''
    }
}

param(
    [ValidateSet('inspect','screenshot','desktop','tree','keys','click','paste','background','menus','text','file','accept','log','diagnostics','maximize')]
    [string]$Action = 'inspect',
    [string]$Value = '',
    [int]$X = 0,
    [int]$Y = 0,
    [int]$Handle = 0,
    [int]$Message = 0,
    [switch]$Right,
    [switch]$Double
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
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
        if(([AutoShopUI]::GetWindowLong($listHandle,-16) -band 0x40) -eq 0){throw 'Owner-drawn compile log does not expose text through LB_GETTEXT; use a screenshot.'}
        $count=[AutoShopUI]::SendMessage($listHandle,0x18B,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
        for($i=0;$i -lt $count;$i++){
            $line=New-Object System.Text.StringBuilder 16384
            [void][AutoShopUI]::SendMessage($listHandle,0x189,[IntPtr]$i,$line)
            Write-Output $line.ToString()
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
        $dialog=$windows|Where-Object {$_.Current.ProcessId -eq $app.Id -and $_.Current.ClassName -eq '#32770' -and $_.Current.IsEnabled -and $_.Current.Name -match '^(另存为|打开|导入|导出)$'}|Select-Object -First 1
        if(!$dialog){throw 'No AutoShop file dialog found.'}
        $controls=$dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $edit=$controls|Where-Object {$_.Current.ClassName -eq 'Edit' -and $_.Current.BoundingRectangle.Width -gt 400}|Select-Object -First 1
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

param(
    [ValidateRange(0,2147483647)][int]$AutoShopProcessId = 0,
    [Parameter(Mandatory=$true)][string]$ExpectedProject,
    [long]$GridHandle = 0,
    [ValidateRange(1,128)][int]$MaxRows = 32,
    [ValidateRange(1,32)][int]$MaxCols = 12,
    [ValidateRange(1,256)][int]$MaxChildren = 64,
    [switch]$ProbeCom,
    [switch]$IncludeControls
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Strictly read-only: getters, accessibility enumeration and window identity.
# No keyboard/mouse input, selection, property setters or PLC library access.
Add-Type -AssemblyName Accessibility
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.InteropServices;
public static class AutoShopOnlineRead {
  public delegate bool EnumProc(IntPtr window, IntPtr parameter);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent,EnumProc callback,IntPtr parameter);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window,StringBuilder value,int count);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder value,int count);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32.dll")] static extern IntPtr GetMenu(IntPtr window);
  [DllImport("user32.dll")] static extern int GetMenuItemCount(IntPtr menu);
  [DllImport("user32.dll")] static extern IntPtr GetSubMenu(IntPtr menu,int position);
  [DllImport("user32.dll")] static extern uint GetMenuItemID(IntPtr menu,int position);
  [DllImport("user32.dll")] static extern uint GetMenuState(IntPtr menu,uint position,uint flags);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetMenuString(IntPtr menu,uint position,StringBuilder text,int capacity,uint flags);
  [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr window,uint objectId,ref Guid interfaceId,[MarshalAs(UnmanagedType.Interface)] out object value);
  [DllImport("oleacc.dll")] static extern int AccessibleChildren([MarshalAs(UnmanagedType.Interface)] object parent,int start,int count,[Out,MarshalAs(UnmanagedType.LPArray,SizeParamIndex=2)] object[] values,out int obtained);
  public static string ClassName(IntPtr window) {var b=new StringBuilder(256);GetClassName(window,b,b.Capacity);return b.ToString();}
  public static string Title(IntPtr window) {var b=new StringBuilder(2048);GetWindowText(window,b,b.Capacity);return b.ToString();}
  public static IntPtr[] Children(IntPtr parent) {var result=new List<IntPtr>();EnumProc callback=(h,p)=>{if(result.Count<2048)result.Add(h);return result.Count<2048;};EnumChildWindows(parent,callback,IntPtr.Zero);return result.ToArray();}
  public static object Accessible(IntPtr window,uint objectId,ref int status) {Guid id=new Guid("00020400-0000-0000-C000-000000000046");object value;status=AccessibleObjectFromWindow(window,objectId,ref id,out value);return status>=0?value:null;}
  static List<object> MenuItems(IntPtr menu,int depth,ref int budget) {
    var result=new List<object>();int count=Math.Min(128,GetMenuItemCount(menu));
    if(depth==0)return result;
    for(int i=0;i<count&&budget>0;i++){
      budget--;var title=new StringBuilder(512);GetMenuString(menu,(uint)i,title,title.Capacity,0x400);
      var item=new Dictionary<string,object>();item["position"]=i;item["name"]=title.ToString();item["command_id"]=GetMenuItemID(menu,i);item["state"]=GetMenuState(menu,(uint)i,0x400);
      IntPtr child=GetSubMenu(menu,i);if(child!=IntPtr.Zero)item["children"]=MenuItems(child,depth-1,ref budget);
      result.Add(item);
    }
    return result;
  }
  public static object Menus(IntPtr window) {int budget=1024;return MenuItems(GetMenu(window),5,ref budget);}
  public static object Get(object value,string name,object[] arguments) {
    if(value==null)throw new ArgumentNullException("value");
    return value.GetType().InvokeMember(name,BindingFlags.GetProperty|BindingFlags.Public|BindingFlags.Instance|BindingFlags.IgnoreCase,null,value,arguments);
  }
  public static string Bounded(object value) {string text=value==null?null:Convert.ToString(value);return text==null?null:text.Substring(0,Math.Min(1024,text.Length));}
  static Dictionary<string,object> Item(Accessibility.IAccessible accessible,object child) {
    var result=new Dictionary<string,object>();result["child_id"]=child is int?child:null;
    try{result["name"]=Bounded(accessible.get_accName(child));}catch(Exception e){result["name_error"]=e.GetType().Name;}
    try{result["value"]=Bounded(accessible.get_accValue(child));}catch(Exception e){result["value_error"]=e.GetType().Name;}
    try{result["role"]=Bounded(accessible.get_accRole(child));}catch{}
    try{result["state"]=Bounded(accessible.get_accState(child));}catch{}
    return result;
  }
  static List<object> Walk(object value,Accessibility.IAccessible acc,int bound,int depth,ref int budget) {
    var children=new List<object>();int count=0;try{count=acc.accChildCount;}catch{return children;}
    int limit=Math.Min(bound,Math.Min(budget,Math.Max(0,count)));
    if(limit==0||depth==0)return children;
    object[] found=new object[limit];int obtained=0;int hr=AccessibleChildren(value,0,limit,found,out obtained);
    if(hr<0)return children;
    for(int i=0;i<obtained&&budget>0;i++){
      budget--;Dictionary<string,object> item;
      if(found[i] is int){item=Item(acc,found[i]);}
      else {var sub=found[i] as Accessibility.IAccessible;if(sub==null)continue;item=Item(sub,0);
        try{item["child_count"]=sub.accChildCount;}catch{}
        item["children"]=Walk(found[i],sub,bound,depth-1,ref budget);
      }
      children.Add(item);
    }
    return children;
  }
  public static object Msaa(object value,int bound) {
    var result=new Dictionary<string,object>();var acc=value as Accessibility.IAccessible;
    if(acc==null){result["supported"]=false;return result;}
    result["supported"]=true;result["self"]=Item(acc,0);
    try{result["child_count"]=acc.accChildCount;}catch(Exception e){result["error"]=e.Message;}
    int budget=Math.Min(4096,bound*12);result["children"]=Walk(value,acc,bound,3,ref budget);
    result["remaining_budget"]=budget;return result;
  }
}
'@ -ReferencedAssemblies @('Accessibility','System')
function Read-GridObject {
    param([object]$Object,[int]$Rows,[int]$Cols)
    $record=[ordered]@{rows=$null;cols=$null;text_matrix=@();errors=@()}
    foreach($property in @('Rows','Cols','Text','TextMatrix')) {
        if($property -eq 'TextMatrix'){continue}
        try {$record[$property.ToLowerInvariant()]=[AutoShopOnlineRead]::Get($Object,$property,@())}
        catch {$record.errors+=($property+': '+$_.Exception.Message.Substring(0,[Math]::Min(300,$_.Exception.Message.Length)))}
    }
    if($record.rows -is [int] -and $record.cols -is [int] -and $record.rows -ge 0 -and $record.cols -ge 0) {
        $table=@()
        for($row=0;$row -lt [Math]::Min($Rows,$record.rows);$row++) {
            $cells=@()
            for($col=0;$col -lt [Math]::Min($Cols,$record.cols);$col++) {
                try {$cells+=[AutoShopOnlineRead]::Bounded([AutoShopOnlineRead]::Get($Object,'TextMatrix',@($row,$col)))}
                catch {$cells+=$null;$record.errors+=('TextMatrix['+$row+','+$col+']: '+$_.Exception.GetType().Name);break}
            }
            $table+=,@($cells)
        }
        $record.text_matrix=$table
    }
    return $record
}
function Test-ProjectTitle {
    param([string]$Title,[string]$Project)
    if($Title -notmatch '^AutoShop\s+V[\d.]+\s+(?<project>[A-Za-z]:\\.+?)\s+-\s+\[[^\]]+\]$'){return $false}
    $actual=[System.IO.Path]::GetFullPath($Matches['project']).TrimEnd('\')
    return [string]::Equals($actual,$Project,[StringComparison]::OrdinalIgnoreCase)
}
try {
    if($ExpectedProject -notmatch '^[A-Za-z]:\\' -or $ExpectedProject.IndexOfAny([char[]]'*?') -ge 0){throw 'ExpectedProject must be an exact absolute Windows project path.'}
    $normalizedProject=[System.IO.Path]::GetFullPath($ExpectedProject).TrimEnd('\')
    if($AutoShopProcessId -eq 0) {
        $matchesProject=@(Get-Process -Name AutoShop -ErrorAction SilentlyContinue|Where-Object {
            $_.MainWindowHandle -ne 0 -and (Test-ProjectTitle -Title $_.MainWindowTitle -Project $normalizedProject)
        })
        if($matchesProject.Count -ne 1){throw ('Expected exactly one AutoShop process for the exact project; found '+$matchesProject.Count+'.')}
        $process=$matchesProject[0]
        $AutoShopProcessId=$process.Id
    } else {$process=Get-Process -Id $AutoShopProcessId -ErrorAction Stop}
    if($process.ProcessName -ne 'AutoShop'){throw 'Expected process is not AutoShop.'}
    $main=[IntPtr]$process.MainWindowHandle
    if($main -eq [IntPtr]::Zero){throw 'AutoShop has no main window.'}
    if(-not (Test-ProjectTitle -Title ([AutoShopOnlineRead]::Title($main)) -Project $normalizedProject)){throw 'AutoShop process does not show the exact expected project.'}
    $handles=@()
    if($GridHandle -gt 0){$handles+=,[IntPtr]$GridHandle}
    else {$handles=@([AutoShopOnlineRead]::Children($main)|Where-Object {[AutoShopOnlineRead]::ClassName($_) -match '^VSFlexGrid'})}
    $allChildren=@([AutoShopOnlineRead]::Children($main))
    $statusControls=@();$toolbarControls=@()
    foreach($control in $allChildren) {
        $controlClass=[AutoShopOnlineRead]::ClassName($control)
        $controlTitle=[AutoShopOnlineRead]::Title($control)
        if([AutoShopOnlineRead]::IsWindowVisible($control) -and ($controlClass -match 'statusbar' -or $controlTitle -match 'PLCRunMode|PLCName|PLCVersion')) {
            [int]$controlStatus=0;$accessible=[AutoShopOnlineRead]::Accessible($control,[uint32]4294967292,[ref]$controlStatus)
            $snapshot=$null;if($null -ne $accessible){$snapshot=[AutoShopOnlineRead]::Msaa($accessible,32)}
            $statusControls+=,[ordered]@{handle=$control.ToInt64();class=$controlClass;title=$controlTitle;hresult=$controlStatus;msaa=$snapshot}
        }
        if($IncludeControls -and $toolbarControls.Count -lt 32 -and [AutoShopOnlineRead]::IsWindowVisible($control) -and $controlClass -match 'toolbar|controlbar') {
            [int]$controlStatus=0;$accessible=[AutoShopOnlineRead]::Accessible($control,[uint32]4294967292,[ref]$controlStatus)
            $snapshot=$null;if($null -ne $accessible){$snapshot=[AutoShopOnlineRead]::Msaa($accessible,64)}
            $toolbarControls+=,[ordered]@{handle=$control.ToInt64();class=$controlClass;title=$controlTitle;hresult=$controlStatus;msaa=$snapshot}
        }
    }
    $buttons=@();$statusElements=@();$menus=@()
    if($IncludeControls) {
        $menus=[AutoShopOnlineRead]::Menus($main)
        $automation=[System.Windows.Automation.AutomationElement]::FromHandle($main)
        $elements=$automation.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        foreach($element in $elements) {
            try {
                $current=$element.Current
                if($current.IsOffscreen){continue}
                if($current.Name -match 'PLCRunMode|PLCName|PLCVersion' -or $current.AutomationId -match 'PLCRunMode|PLCName|PLCVersion') {
                    $legacyValue=$null;$directValue=$null
                    try{$legacyValue=$element.GetCurrentPropertyValue([System.Windows.Automation.LegacyIAccessiblePattern]::ValueProperty)}catch{}
                    try{$directValue=$element.GetCurrentPropertyValue([System.Windows.Automation.ValuePattern]::ValueProperty)}catch{}
                    $statusElements+=,[ordered]@{name=$current.Name;automation_id=$current.AutomationId;class=$current.ClassName;handle=$current.NativeWindowHandle;legacy_value=$legacyValue;value=$directValue}
                }
                if($buttons.Count -ge 256 -or ($current.ControlType -ne [System.Windows.Automation.ControlType]::Button -and $current.ControlType -ne [System.Windows.Automation.ControlType]::ToolBar)){continue}
                $bounds=$current.BoundingRectangle
                $buttons+=,[ordered]@{name=$current.Name;automation_id=$current.AutomationId;class=$current.ClassName;handle=$current.NativeWindowHandle;control_type=$current.ControlType.ProgrammaticName;enabled=$current.IsEnabled;access_key=$current.AccessKey;accelerator_key=$current.AcceleratorKey;help_text=$current.HelpText;invoke_available=$element.GetCurrentPropertyValue([System.Windows.Automation.AutomationElement]::IsInvokePatternAvailableProperty);bounds=@($bounds.X,$bounds.Y,$bounds.Width,$bounds.Height)}
            } catch {}
        }
    }
    $grids=@()
    foreach($handle in $handles) {
        [uint32]$owner=0
        [void][AutoShopOnlineRead]::GetWindowThreadProcessId($handle,[ref]$owner)
        $class=[AutoShopOnlineRead]::ClassName($handle)
        if($owner -ne $AutoShopProcessId -or -not [AutoShopOnlineRead]::IsWindow($handle) -or $class -notmatch '^VSFlexGrid') {throw 'Grid is not an existing VSFlexGrid owned by the expected AutoShop process.'}
        $grid=[ordered]@{handle=$handle.ToInt64();class=$class;visible=[AutoShopOnlineRead]::IsWindowVisible($handle);objects=@()}
        $objectIds=@([uint32]4294967292)
        if($ProbeCom){$objectIds=@([uint32]4294967280,[uint32]4294967292,[uint32]0)}
        foreach($objectId in $objectIds) {
            [int]$status=0
            $value=[AutoShopOnlineRead]::Accessible($handle,$objectId,[ref]$status)
            $entry=[ordered]@{object_id=$objectId;hresult=$status;available=($null -ne $value)}
            if($null -ne $value) {
                if($ProbeCom){try {$entry.grid_properties=Read-GridObject -Object $value -Rows $MaxRows -Cols $MaxCols}catch{$entry.property_error=$_.Exception.Message}}
                try {$entry.msaa=[AutoShopOnlineRead]::Msaa($value,$MaxChildren)}catch{$entry.msaa_error=$_.Exception.Message}
            }
            $grid.objects+=,$entry
        }
        $grids+=,$grid
    }
    $finalTitle=[AutoShopOnlineRead]::Title($main)
    if(-not (Test-ProjectTitle -Title $finalTitle -Project $normalizedProject)){throw 'AutoShop project changed during the read-only capture.'}
    [ordered]@{schema_version=1;ok=$true;status='read-only-ui-probe';process_id=$AutoShopProcessId;main_handle=$main.ToInt64();title=$finalTitle;expected_project=$normalizedProject;captured_at_utc=[DateTime]::UtcNow.ToString('o');grids=$grids;status_controls=$statusControls;toolbar_controls=$toolbarControls;accessible_buttons=$buttons;status_elements=$statusElements;menus=$menus;plc_state='unverified';limitations=@('UI property inspection only; no PLC protocol calls','No state inferred from colors or a successful UI request')}|ConvertTo-Json -Depth 20 -Compress
} catch {
    [ordered]@{schema_version=1;ok=$false;status='unverified';error=$_.Exception.Message;plc_state='unverified'}|ConvertTo-Json -Depth 4 -Compress
    exit 1
}

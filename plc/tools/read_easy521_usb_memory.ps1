param(
    [switch]$Execute,
    [ValidateRange(0,65535)][int]$Sequence = 4660,
    [ValidateSet('revision','step','scan-counter','confidence','published-scan','published-value','heartbeat','m8000','m8001','m8013')]
    [string]$ReadProfile = 'revision'
)
# Fixed selector3 READ candidates from research19 and the pinned GVT/CSV.
# This is Windows libusb0 transport, not native Linux USB. No arbitrary address,
# PLC write, CPU mode change, configuration change, reset, clear-halt, download,
# AutoShop close or request replay is implemented. Response decoding belongs to
# Python's independent candidate codec; raw-frame-only never asserts a value.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$targets = @{
    'revision'=@(0x02100140,32); 'step'=@(0x02100160,32)
    'scan-counter'=@(0x02100130,16); 'confidence'=@(0x021001c0,32)
    'published-scan'=@(0x02100180,32); 'published-value'=@(0x021001a0,32)
    'heartbeat'=@(0x02100003,1); 'm8000'=@(0x06201800,1)
    'm8001'=@(0x06201801,1); 'm8013'=@(0x06201822,1)
}
if (!$Execute) {
    [byte[]]$request = (22,0,1,3,($Sequence -band 255),(($Sequence -shr 8) -band 255),0,0,1,0)
    $request += [BitConverter]::GetBytes([uint32]1)
    $request += [BitConverter]::GetBytes([uint32]($targets[$ReadProfile][0]))
    $request += [BitConverter]::GetBytes([uint32]($targets[$ReadProfile][1]))
    [ordered]@{schema='plcidf.usb-memory-probe.v1';status='preview';device_access=$false;read_profile=$ReadProfile;request_hex=([BitConverter]::ToString($request).Replace('-','').ToLowerInvariant())}|ConvertTo-Json -Compress
    exit 0
}
$library = 'E:\Software\AutoShop\libusb0.dll'
$expectedHash = 'cb6ac96e9abf55586622af809b98af1c989b50d490b2e0770dd7fbe54352520c'
if ([IntPtr]::Size -ne 8) { throw 'The pinned driver library requires an x64 host.' }
if ((Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Installed libusb0 library differs from the inspected file.'
}
if (@(Get-Process -Name AutoShop -ErrorAction SilentlyContinue).Count -ne 0) {
    throw 'AutoShop must already be closed; no memory request sent.'
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class PlcUsbMemoryRead {
    const string Dll = @"E:\Software\AutoShop\libusb0.dll";
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern void usb_init();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_find_busses();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_find_devices();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_get_busses();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_open(IntPtr device);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_close(IntPtr handle);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_get_descriptor(IntPtr handle, byte type, byte index, [Out] byte[] data, int size);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_control_msg(IntPtr handle, int requestType, int request, int value, int index, [Out] byte[] data, int size, int timeout);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_strerror();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_claim_interface(IntPtr handle, int iface);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_release_interface(IntPtr handle, int iface);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_bulk_write(IntPtr handle, int endpoint, byte[] data, int size, int timeout);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_bulk_read(IntPtr handle, int endpoint, [Out] byte[] data, int size, int timeout);
    static string Error() { return Marshal.PtrToStringAnsi(usb_strerror()); }

    // Byte-packed libusb-win32 x64 ABI, not the old x86 list offsets.
    static List<IntPtr> Matches() {
        var result = new List<IntPtr>(); var buses = new HashSet<IntPtr>();
        for (IntPtr bus=usb_get_busses(); bus!=IntPtr.Zero; bus=Marshal.ReadIntPtr(bus)) {
            if (buses.Count>=16 || !buses.Add(bus)) throw new InvalidOperationException("Invalid bus list");
            var devices = new HashSet<IntPtr>();
            for (IntPtr dev=Marshal.ReadIntPtr(bus,528); dev!=IntPtr.Zero; dev=Marshal.ReadIntPtr(dev)) {
                if (devices.Count>=256 || !devices.Add(dev)) throw new InvalidOperationException("Invalid device list");
                if ((ushort)Marshal.ReadInt16(dev,544)!=0x0471 || (ushort)Marshal.ReadInt16(dev,546)!=0xa531) continue;
                if (Marshal.ReadByte(dev,536)!=18 || Marshal.ReadByte(dev,537)!=1) throw new InvalidOperationException("Descriptor ABI mismatch");
                result.Add(dev);
            }
        }
        return result;
    }

    // Explicit target switch is the only way to form a request. Addresses are
    // opaque symbol values copied unchanged; width is bits, never bytes.
    static byte[] Request(ushort sequence, string profile) {
        uint address; uint bits;
        switch(profile) {
            case "revision": address=0x02100140; bits=32; break;
            case "step": address=0x02100160; bits=32; break;
            case "scan-counter": address=0x02100130; bits=16; break;
            case "confidence": address=0x021001c0; bits=32; break;
            case "published-scan": address=0x02100180; bits=32; break;
            case "published-value": address=0x021001a0; bits=32; break;
            case "heartbeat": address=0x02100003; bits=1; break;
            case "m8000": address=0x06201800; bits=1; break;
            case "m8001": address=0x06201801; bits=1; break;
            case "m8013": address=0x06201822; bits=1; break;
            default: throw new InvalidOperationException("Unknown fixed profile");
        }
        byte[] request = new byte[22];
        request[0]=22; request[2]=1; request[3]=3; request[8]=1;
        request[4]=(byte)(sequence & 255); request[5]=(byte)(sequence >> 8);
        Buffer.BlockCopy(BitConverter.GetBytes((uint)1),0,request,10,4);
        Buffer.BlockCopy(BitConverter.GetBytes(address),0,request,14,4);
        Buffer.BlockCopy(BitConverter.GetBytes(bits),0,request,18,4);
        return request;
    }

    public static object ReadMemory(ushort sequence, string profile) {
        var record = new Dictionary<string,object>();
        record["ok"]=false; record["stage"]="enumerate"; record["write_calls"]=0;
        record["value_semantics"]="candidate";
        IntPtr handle=IntPtr.Zero; bool claimed=false;
        try {
            byte[] request=Request(sequence,profile); // Validate profile before USB APIs.
            usb_init();
            if (usb_find_busses()<0 || usb_find_devices()<0) throw new InvalidOperationException(Error());
            List<IntPtr> matches=Matches();
            if (matches.Count!=1) throw new InvalidOperationException("Expected exactly one 0471:a531");
            handle=usb_open(matches[0]);
            if (handle==IntPtr.Zero) throw new InvalidOperationException("usb_open: "+Error());
            byte[] descriptor=new byte[18];
            int descriptorRc=usb_get_descriptor(handle,1,0,descriptor,18);
            if (descriptorRc!=18 || descriptor[0]!=18 || descriptor[1]!=1 || BitConverter.ToUInt16(descriptor,8)!=0x0471 || BitConverter.ToUInt16(descriptor,10)!=0xa531)
                throw new InvalidOperationException("Descriptor mismatch");
            record["descriptor_hex"]=BitConverter.ToString(descriptor).Replace("-","").ToLowerInvariant();
            byte[] config=new byte[1];
            int configRc=usb_control_msg(handle,0x80,8,0,0,config,1,1000);
            record["configuration_rc"]=configRc;
            if (configRc!=1 || config[0]!=2) throw new InvalidOperationException("Already-active configuration2 required; no change made");
            record["stage"]="claim";
            int claimRc=usb_claim_interface(handle,0); record["claim_rc"]=claimRc;
            if (claimRc!=0) throw new InvalidOperationException("Interface unavailable: "+Error());
            claimed=true;
            record["request_hex"]=BitConverter.ToString(request).Replace("-","").ToLowerInvariant();
            record["stage"]="send-read-request"; record["write_calls"]=1;
            int sent=usb_bulk_write(handle,0x02,request,request.Length,2500);
            record["bulk_write_rc"]=sent;
            if (sent!=22) throw new InvalidOperationException("Incomplete read request; no replay: "+Error());
            record["stage"]="receive";
            var bytes=new List<byte>(); var reads=new List<int>();
            var deadline=System.Diagnostics.Stopwatch.StartNew(); int expected=-1;
            for (int attempt=0; attempt<4 && deadline.ElapsedMilliseconds<2500; attempt++) {
                byte[] chunk=new byte[512];
                int timeout=(int)Math.Max(1,2500-deadline.ElapsedMilliseconds);
                int received=usb_bulk_read(handle,0x81,chunk,chunk.Length,timeout);
                reads.Add(received); record["read_rcs"]=reads;
                if (received<=0 || received>chunk.Length) throw new InvalidOperationException("No complete response; no request replay: "+Error());
                for (int i=0;i<received;i++) bytes.Add(chunk[i]);
                record["response_hex"]=BitConverter.ToString(bytes.ToArray()).Replace("-","").ToLowerInvariant();
                if (bytes.Count>=2) expected=bytes[0] | (bytes[1]<<8);
                if (expected>26 || (expected>=0 && expected<10) || bytes.Count>26) throw new InvalidOperationException("Response exceeds fixed scalar bounds");
                if (expected>=0 && bytes.Count>=expected) {
                    if (bytes.Count!=expected) throw new InvalidOperationException("Trailing response bytes");
                    record["ok"]=true; record["stage"]="raw-frame-only";
                    return record; // Independent Python codec still must accept every field.
                }
            }
            throw new InvalidOperationException("Incomplete frame within read budget");
        } catch (Exception error) { record["error"]=error.Message; return record; }
        finally {
            if (claimed) record["release_rc"]=usb_release_interface(handle,0);
            if (handle!=IntPtr.Zero) record["close_rc"]=usb_close(handle);
        }
    }
}
'@
[ordered]@{
    schema='plcidf.usb-memory-probe.v1';observed_at=[DateTimeOffset]::Now.ToString('o')
    scope='single fixed memory read via Windows libusb0; no variable or application change'
    read_profile=$ReadProfile;library_sha256=$expectedHash
    result=[PlcUsbMemoryRead]::ReadMemory([uint16]$Sequence,$ReadProfile)
}|ConvertTo-Json -Depth 8 -Compress

param(
    [switch]$Execute,
    [ValidateRange(0,65535)][int]$Sequence = 4660,
    [ValidateSet('literal','gcm-state')][string]$ReadProfile = 'literal'
)
# Experimental fixed vendor READs via Windows libusb0; no arbitrary index.
# Default is preview. Execute requires AutoShop already closed; this helper
# never closes applications, changes configuration, resets endpoints/devices,
# clears halts, writes variables, changes CPU mode or downloads applications.
# Actual 2026-10-19 evidence: evidence/easy521-ai-loop-20261019/
# single-usb-runtime-query.json. The historical literal ff010000/4 read returned
# raw zero. Research 18 corrects its former runtime interpretation: that command
# belongs to CReadEthercatCommand and cannot identify CPU RUN/STOP. A separate
# vendor GetGCMState chain specifies ff000000/1; its static 0/1 mapping still
# needs real-device interpretation checks. The filename is kept for compatibility.
# The adjacent Python worker checks fresh USB location, owns a cooperating
# lease, calls this helper once and validates the full response using the
# offline codec. This PowerShell result alone is transport data, not a CPU
# state assertion. Only the inspected x64 driver library is supported.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$library = 'E:\Software\AutoShop\libusb0.dll'
$expectedHash = 'cb6ac96e9abf55586622af809b98af1c989b50d490b2e0770dd7fbe54352520c'
if ([IntPtr]::Size -ne 8) { throw 'The inspected libusb0 library requires a 64-bit host.' }
if ((Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Installed libusb0 library differs from the inspected file.'
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class PlcUsbDescriptors {
    const string Dll = @"E:\Software\AutoShop\libusb0.dll";
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern void usb_init();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_find_busses();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_find_devices();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_get_busses();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_get_version();
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_open(IntPtr dev);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_close(IntPtr handle);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_get_descriptor(IntPtr handle, byte type, byte index, byte[] data, int size);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_get_string_simple(IntPtr handle, int index, byte[] data, UIntPtr size);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_control_msg(IntPtr handle, int requestType, int request, int value, int index, byte[] data, int size, int timeout);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern IntPtr usb_strerror();

    static string Error() { return Marshal.PtrToStringAnsi(usb_strerror()); }

    // Library-owned lists use Pack=1, including pointers after the 512-byte
    // path. Bounds and cycle detection guard traversal of this fixed ABI.
    static List<IntPtr> Matches() {
        var matches = new List<IntPtr>();
        var buses = new HashSet<IntPtr>();
        for (IntPtr bus = usb_get_busses(); bus != IntPtr.Zero; bus = Marshal.ReadIntPtr(bus)) {
            if (buses.Count >= 16 || !buses.Add(bus)) throw new InvalidOperationException("Invalid USB bus list");
            var devices = new HashSet<IntPtr>();
            IntPtr first = Marshal.ReadIntPtr(bus, 2 * IntPtr.Size + 512);
            for (IntPtr dev = first; dev != IntPtr.Zero; dev = Marshal.ReadIntPtr(dev)) {
                if (devices.Count >= 256 || !devices.Add(dev)) throw new InvalidOperationException("Invalid USB device list");
                int offset = 3 * IntPtr.Size + 512;
                int vid = (ushort)Marshal.ReadInt16(dev, offset + 8);
                int pid = (ushort)Marshal.ReadInt16(dev, offset + 10);
                if (vid != 0x0471 || pid != 0xa531) continue;
                if (Marshal.ReadByte(dev, offset) != 18 || Marshal.ReadByte(dev, offset + 1) != 1)
                    throw new InvalidOperationException("Unexpected matching descriptor ABI");
                matches.Add(dev);
            }
        }
        return matches;
    }

    static object ReadString(IntPtr handle, int index) {
        if (index == 0) return new { index = 0, status = "absent", text = "", error = "" };
        byte[] data = new byte[128];
        int count = usb_get_string_simple(handle, index, data, new UIntPtr((uint)data.Length));
        return new { index = index, status = count >= 0 ? "read" : "failed",
            text = count >= 0 ? Encoding.ASCII.GetString(data, 0, Math.Min(count, data.Length)) : "",
            error = count < 0 ? Error() : "" };
    }


    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_claim_interface(IntPtr handle, int iface);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_release_interface(IntPtr handle, int iface);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_bulk_write(IntPtr handle, int endpoint, byte[] data, int count, int timeout);
    [DllImport(Dll, CallingConvention=CallingConvention.Cdecl)] static extern int usb_bulk_read(IntPtr handle, int endpoint, [Out] byte[] data, int count, int timeout);

    // One fixed index READ. No configuration change, clear-halt, reset,
    // arbitrary operation, CPU mode command, download or retry is provided.
    public static object ReadRuntime(ushort sequence, bool gcmState) {
        var record = new Dictionary<string, object>();
        record["ok"] = false; record["stage"] = "enumerate";
        record["write_calls"] = 0; record["state_semantics"] = "unknown";
        IntPtr handle = IntPtr.Zero; bool claimed = false;
        try {
            usb_init();
            if (usb_find_busses() < 0 || usb_find_devices() < 0) throw new InvalidOperationException(Error());
            List<IntPtr> devices = Matches();
            if (devices.Count != 1) throw new InvalidOperationException("Expected exactly one 0471:a531");
            handle = usb_open(devices[0]);
            if (handle == IntPtr.Zero) throw new InvalidOperationException("usb_open: " + Error());
            byte[] descriptor = new byte[18];
            int descriptorRc = usb_get_descriptor(handle, 1, 0, descriptor, descriptor.Length);
            if (descriptorRc != 18 || descriptor[0] != 18 || descriptor[1] != 1 || BitConverter.ToUInt16(descriptor,8) != 0x0471 || BitConverter.ToUInt16(descriptor,10) != 0xa531)
                throw new InvalidOperationException("Descriptor mismatch");
            record["descriptor_hex"] = BitConverter.ToString(descriptor).Replace("-", "").ToLowerInvariant();
            byte[] config = new byte[1];
            int configRc = usb_control_msg(handle, 0x80, 8, 0, 0, config, 1, 1000);
            record["configuration_rc"] = configRc;
            if (configRc != 1 || config[0] != 2) throw new InvalidOperationException("Already-active configuration 2 is required; no setting changed");
            record["stage"] = "claim";
            int claimRc = usb_claim_interface(handle, 0); record["claim_rc"] = claimRc;
            if (claimRc < 0) throw new InvalidOperationException("Interface unavailable: " + Error());
            claimed = true;
            byte[] request = new byte[] { 0x12, 0x00, 0x01, 0x01, 0x34, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0xff, 0x04, 0x00, 0x00, 0x00 };
            // Exactly two inspected read contracts are available. The GCM
            // contract comes from the actual vendor CGCMStateCommand dd30,
            // not an assumption based on the separately shipped Simulator.
            // The historical literal contract remains a raw index observation.
            if (gcmState) { request[12] = 0; request[14] = 1; }
            // The correlation word varies; neither contract permits a writer.
            request[4] = (byte)(sequence & 255); request[5] = (byte)(sequence >> 8);
            record["request_hex"] = BitConverter.ToString(request).Replace("-", "").ToLowerInvariant();
            record["stage"] = "send-read-request"; record["write_calls"] = 1;
            int sent = usb_bulk_write(handle, 0x02, request, request.Length, 2500);
            record["bulk_write_rc"] = sent;
            if (sent != request.Length) throw new InvalidOperationException("Incomplete request; no retry: " + Error());
            record["stage"] = "receive";
            var bytes = new List<byte>(); var reads = new List<int>();
            var deadline = System.Diagnostics.Stopwatch.StartNew(); int expected = -1;
            for (int attempt = 0; attempt < 4 && deadline.ElapsedMilliseconds < 2500; attempt++) {
                byte[] chunk = new byte[512];
                int timeout = (int)Math.Max(1, 2500 - deadline.ElapsedMilliseconds);
                int received = usb_bulk_read(handle, 0x81, chunk, chunk.Length, timeout);
                reads.Add(received); record["read_rcs"] = reads;
                if (received <= 0) throw new InvalidOperationException("No complete response; no request replay: " + Error());
                for (int i=0;i<received;i++) bytes.Add(chunk[i]);
                record["response_hex"] = BitConverter.ToString(bytes.ToArray()).Replace("-", "").ToLowerInvariant();
                if (bytes.Count >= 2) expected = bytes[0] | (bytes[1] << 8);
                // Error frames contain a 4-byte code even when the requested
                // data is one byte, so the transport permits up to 14 bytes.
                // Operation-specific lengths are checked by Python's codec.
                const int maximumFrame = 14;
                if (expected > maximumFrame || (expected >= 0 && expected < 10) || bytes.Count > maximumFrame)
                    throw new InvalidOperationException("Response outside fixed profile length");
                if (expected >= 0 && bytes.Count >= expected) {
                    if (bytes.Count != expected) throw new InvalidOperationException("Trailing response bytes");
                    record["ok"] = true; record["stage"] = "raw-frame-only";
                    // Python's independent codec must still check operation,
                    // sequence and data/error shape; transport completion is
                    // not a CPU state result or a device identity guarantee.
                    return record;
                }
            }
            throw new InvalidOperationException("Incomplete frame within read budget");
        } catch (Exception error) {
            record["error"] = error.Message; return record;
        } finally {
            if (claimed) record["release_rc"] = usb_release_interface(handle, 0);
            if (handle != IntPtr.Zero) record["close_rc"] = usb_close(handle);
        }
    }

    public static object Read() {
        usb_init();
        IntPtr versionPointer = usb_get_version();
        int[] versions = new int[8];
        if (versionPointer == IntPtr.Zero) throw new InvalidOperationException("Missing library version");
        Marshal.Copy(versionPointer, versions, 0, versions.Length);
        if (usb_find_busses() < 0 || usb_find_devices() < 0) throw new InvalidOperationException(Error());
        List<IntPtr> devices = Matches();
        if (devices.Count != 1) throw new InvalidOperationException("Expected exactly one 0471:a531 device, found " + devices.Count);
        IntPtr handle = usb_open(devices[0]);
        if (handle == IntPtr.Zero) throw new InvalidOperationException("usb_open failed: " + Error());
        try {
            byte[] descriptor = new byte[18];
            int count = usb_get_descriptor(handle, 1, 0, descriptor, descriptor.Length);
            if (count != 18 || descriptor[0] != 18 || descriptor[1] != 1 || BitConverter.ToUInt16(descriptor, 8) != 0x0471 || BitConverter.ToUInt16(descriptor, 10) != 0xa531)
                throw new InvalidOperationException("Standard device descriptor did not match: " + Error());
            byte[] configuration = new byte[1];
            // USB GET_CONFIGURATION: standard device-to-host request only.
            int configResult = usb_control_msg(handle, 0x80, 8, 0, 0, configuration, 1, 1000);
            return new { library_driver_versions = versions, device_descriptor_hex = BitConverter.ToString(descriptor).Replace("-", "").ToLowerInvariant(),
                configuration_read_rc = configResult, configuration = configResult == 1 ? (int?)configuration[0] : null,
                manufacturer = ReadString(handle, descriptor[14]), product = ReadString(handle, descriptor[15]),
                configuration_count = descriptor[17], vidpid = "0471:a531" };
        } finally { usb_close(handle); }
    }
}
'@


if (!$Execute) {
    $previewRequest = [byte[]](0x12,0,1,1,($Sequence -band 255),(($Sequence -shr 8) -band 255),0,0,1,0,0,0,1,0xff,4,0,0,0)
    if ($ReadProfile -eq 'gcm-state') { $previewRequest[12] = 0; $previewRequest[14] = 1 }
    [ordered]@{schema='plcidf.usb-runtime-probe.v1';status='preview';device_access=$false;read_profile=$ReadProfile;request_hex=([BitConverter]::ToString($previewRequest).Replace('-','').ToLowerInvariant())}|ConvertTo-Json -Compress
    exit 0
}
if (@(Get-Process -Name AutoShop -ErrorAction SilentlyContinue).Count -ne 0) {
    throw 'AutoShop must be closed for this exclusive USB experiment; no PLC request sent.'
}
[ordered]@{schema='plcidf.usb-runtime-probe.v1';observed_at=[DateTimeOffset]::Now.ToString('o');scope='single fixed read request via Windows libusb0; no CPU mode or application change';read_profile=$ReadProfile;library_sha256=$expectedHash;result=[PlcUsbDescriptors]::ReadRuntime([uint16]$Sequence, ($ReadProfile -eq 'gcm-state'))}|ConvertTo-Json -Depth 8 -Compress

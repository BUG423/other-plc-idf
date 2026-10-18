# Read standard USB descriptors through the already-installed libusb0 driver.
# No configuration change, interface claim, bulk transfer, reset or PLC command
# is implemented here. This helper establishes descriptor-level observations,
# not CPU identity, current PLC firmware, RUN state or application execution.
# Struct offsets follow libusb-win32's byte-packed lusb0_usb.h; descriptor
# lengths/types and an exact, unique VID/PID match are checked before opening.
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

try {
    $result = [PlcUsbDescriptors]::Read()
    [ordered]@{
        schema = 'plcidf.usb-descriptor-observation.v1'
        observed_at = [DateTimeOffset]::Now.ToString('o')
        ok = $true
        scope = 'standard USB descriptors only; no bulk PLC protocol'
        library = $library
        library_sha256 = $expectedHash
        cpu_identity_verified = $false
        cpu_state_verified = $false
        result = $result
    } | ConvertTo-Json -Depth 6 -Compress
} catch {
    [ordered]@{schema='plcidf.usb-descriptor-observation.v1';ok=$false;scope='standard USB descriptors only';error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}

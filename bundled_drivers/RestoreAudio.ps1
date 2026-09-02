param (
    [Parameter(Mandatory=$true)]
    [ValidateSet("Save", "Restore")]
    [string]$Action,

    [string]$TempFile = "$env:TEMP\saved_default_audio_device.txt"
)

$CsharpCode = @"
using System;
using System.Runtime.InteropServices;

public enum ERole : uint {
    eConsole = 0,
    eMultimedia = 1,
    eCommunications = 2,
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr ppDevices);
    [PreserveSig]
    int GetDefaultAudioEndpoint(int dataFlow, ERole role, out IMMDevice ppEndpoint);
    int GetDevice([In, MarshalAs(UnmanagedType.LPWStr)] string pwstrId, out IMMDevice ppDevice);
    int RegisterEndpointNotificationCallback(IntPtr pClient);
    int UnregisterEndpointNotificationCallback(IntPtr pClient);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    [PreserveSig]
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, out IntPtr ppInterface);
    int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);
    [PreserveSig]
    int GetId([Out, MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
    int GetState(out int pdwState);
}

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class MMDeviceEnumeratorComObject { }

// Windows 10/11
[Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPolicyConfig {
    [PreserveSig] int GetMixFormat(string pszDeviceName, out IntPtr ppFormat);
    [PreserveSig] int GetDeviceFormat(string pszDeviceName, int bDefault, out IntPtr ppFormat);
    [PreserveSig] int ResetDeviceFormat(string pszDeviceName);
    [PreserveSig] int SetDeviceFormat(string pszDeviceName, IntPtr pEndpointFormat, IntPtr mixFormat);
    [PreserveSig] int GetProcessingPeriod(string pszDeviceName, int bDefault, out IntPtr pmftDefaultPeriod, out IntPtr pmftMinimumPeriod);
    [PreserveSig] int SetProcessingPeriod(string pszDeviceName, IntPtr pmftPeriod);
    [PreserveSig] int GetShareMode(string pszDeviceName, out IntPtr pMode);
    [PreserveSig] int SetShareMode(string pszDeviceName, IntPtr mode);
    [PreserveSig] int GetPropertyValue(string pszDeviceName, int bFxStore, IntPtr key, out IntPtr pv);
    [PreserveSig] int SetPropertyValue(string pszDeviceName, int bFxStore, IntPtr key, IntPtr pv);
    [PreserveSig] int SetDefaultEndpoint(string pszDeviceName, ERole role);
    [PreserveSig] int SetEndpointVisibility(string pszDeviceName, int bVisible);
}

// Windows 7/8/10 older
[Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPolicyConfigLegacy {
    [PreserveSig] int GetMixFormat(string pszDeviceName, out IntPtr ppFormat);
    [PreserveSig] int GetDeviceFormat(string pszDeviceName, int bDefault, out IntPtr ppFormat);
    [PreserveSig] int ResetDeviceFormat(string pszDeviceName);
    [PreserveSig] int SetDeviceFormat(string pszDeviceName, IntPtr pEndpointFormat, IntPtr mixFormat);
    [PreserveSig] int GetProcessingPeriod(string pszDeviceName, int bDefault, out IntPtr pmftDefaultPeriod, out IntPtr pmftMinimumPeriod);
    [PreserveSig] int SetProcessingPeriod(string pszDeviceName, IntPtr pmftPeriod);
    [PreserveSig] int GetShareMode(string pszDeviceName, out IntPtr pMode);
    [PreserveSig] int SetShareMode(string pszDeviceName, IntPtr mode);
    [PreserveSig] int GetPropertyValue(string pszDeviceName, int bFxStore, IntPtr key, out IntPtr pv);
    [PreserveSig] int SetPropertyValue(string pszDeviceName, int bFxStore, IntPtr key, IntPtr pv);
    [PreserveSig] int SetDefaultEndpoint(string pszDeviceName, ERole role);
    [PreserveSig] int SetEndpointVisibility(string pszDeviceName, int bVisible);
}

[ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
public class PolicyConfigClientComObject { }

public class AudioController {
    public static string GetDefaultRenderDeviceId() {
        IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
        IMMDevice device = null;
        enumerator.GetDefaultAudioEndpoint(0, ERole.eMultimedia, out device);
        if (device != null) {
            string id;
            device.GetId(out id);
            return id;
        }
        return null;
    }

    public static void SetDefaultRenderDevice(string deviceId) {
        object policyConfigObj = new PolicyConfigClientComObject();
        try {
            IPolicyConfig policyConfig = (IPolicyConfig)policyConfigObj;
            policyConfig.SetDefaultEndpoint(deviceId, ERole.eConsole);
            policyConfig.SetDefaultEndpoint(deviceId, ERole.eMultimedia);
            policyConfig.SetDefaultEndpoint(deviceId, ERole.eCommunications);
        } catch (InvalidCastException) {
            IPolicyConfigLegacy policyConfigLegacy = (IPolicyConfigLegacy)policyConfigObj;
            policyConfigLegacy.SetDefaultEndpoint(deviceId, ERole.eConsole);
            policyConfigLegacy.SetDefaultEndpoint(deviceId, ERole.eMultimedia);
            policyConfigLegacy.SetDefaultEndpoint(deviceId, ERole.eCommunications);
        }
    }
}
"@

Add-Type -TypeDefinition $CsharpCode

if ($Action -eq "Save") {
    $deviceId = [AudioController]::GetDefaultRenderDeviceId()
    if ([string]::IsNullOrEmpty($deviceId)) {
        Write-Error "Could not retrieve default audio device ID."
        exit 1
    }
    Write-Host "Saving Default Audio Device ID: $deviceId"
    $deviceId | Out-File -FilePath $TempFile -Encoding UTF8
    Write-Host "Saved to $TempFile"
}
elseif ($Action -eq "Restore") {
    if (-Not (Test-Path $TempFile)) {
        Write-Error "Saved device ID file not found."
        exit 1
    }
    $deviceId = Get-Content -Path $TempFile -Raw
    $deviceId = $deviceId.Trim()
    
    if ([string]::IsNullOrEmpty($deviceId)) {
        Write-Error "Saved device ID is empty."
        exit 1
    }
    
    Write-Host "Restoring Default Audio Device ID: $deviceId"
    [AudioController]::SetDefaultRenderDevice($deviceId)
    Write-Host "Restoration complete."
    
    Remove-Item -Path $TempFile -Force
}

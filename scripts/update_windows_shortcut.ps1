# Point an existing Route Desk shortcut at the route-mark icon and the
# same AppUserModelID the running app sets (eagleklawjlv.RouteDesk).
#
# This does not change the shortcut target, arguments, or working directory.
# It does not read %APPDATA%\EveRouteDesk, tokens, settings, library files,
# or the install .venv. Close Route Desk before running it.
param(
    [string]$InstallDir = "C:\Users\Johnl\AppData\Local\Programs\EveRouteDesk",
    [string]$ShortcutPath = ""
)

$ErrorActionPreference = "Stop"
$AppUserModelId = "eagleklawjlv.RouteDesk"
$ShortcutName = "EVE Route Desk.lnk"
$IconPath = Join-Path $InstallDir "everoute\assets\route-desk.ico"

if (-not (Test-Path -LiteralPath $IconPath)) {
    throw "Icon not found: $IconPath. Overlay the update onto the install folder before running this script."
}
$IconPath = (Resolve-Path -LiteralPath $IconPath).Path

$signature = @"
using System;
using System.Runtime.InteropServices;

public static class RouteDeskShortcut {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr psz;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        void Commit();
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class CShellLink {}

    [ComImport, Guid("0000010B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistFile {
        void GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile(out IntPtr ppszFileName);
    }

    public static void SetAppUserModelId(string shortcutPath, string appId) {
        IPersistFile file = (IPersistFile)new CShellLink();
        file.Load(shortcutPath, 2);
        IPropertyStore store = (IPropertyStore)file;
        PROPERTYKEY key = new PROPERTYKEY();
        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        key.pid = 5;
        PROPVARIANT value = new PROPVARIANT();
        value.vt = 31;
        value.psz = Marshal.StringToCoTaskMemUni(appId);
        try {
            store.SetValue(ref key, ref value);
            store.Commit();
        } finally {
            if (value.psz != IntPtr.Zero) {
                Marshal.FreeCoTaskMem(value.psz);
            }
        }
        file.Save(shortcutPath, true);
    }
}
"@

if (-not ("RouteDeskShortcut" -as [type])) {
    Add-Type -TypeDefinition $signature -Language CSharp
}

function Get-ShortcutPaths {
    if ($ShortcutPath) {
        if (-not (Test-Path -LiteralPath $ShortcutPath)) {
            throw "Shortcut not found: $ShortcutPath"
        }
        return @((Resolve-Path -LiteralPath $ShortcutPath).Path)
    }
    $dirs = New-Object System.Collections.Generic.List[string]
    foreach ($name in @("Desktop", "CommonDesktopDirectory", "StartMenu", "Programs", "CommonPrograms")) {
        $folder = [Environment]::GetFolderPath($name)
        if ($folder) { $dirs.Add($folder) }
    }
    $pinned = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
    if ($pinned) { $dirs.Add($pinned) }
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($dir in $dirs) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        Get-ChildItem -LiteralPath $dir -Filter $ShortcutName -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
            if (-not $found.Contains($_.FullName)) { $found.Add($_.FullName) }
        }
    }
    $beside = Join-Path $InstallDir $ShortcutName
    if ((Test-Path -LiteralPath $beside) -and -not $found.Contains($beside)) {
        $found.Add($beside)
    }
    return $found
}

$paths = @(Get-ShortcutPaths)
if ($paths.Count -eq 0) {
    Write-Error "No '$ShortcutName' was found on the desktop, Start menu, or taskbar pins. Pass -ShortcutPath with the full .lnk path. Nothing was changed."
    exit 2
}

$shell = New-Object -ComObject WScript.Shell
$updated = 0
foreach ($path in $paths) {
    $before = $shell.CreateShortcut($path)
    $target = $before.TargetPath
    $arguments = $before.Arguments
    $workdir = $before.WorkingDirectory
    $before.IconLocation = "$IconPath,0"
    $before.Save()
    [RouteDeskShortcut]::SetAppUserModelId($path, $AppUserModelId)
    $after = $shell.CreateShortcut($path)
    if ($after.TargetPath -ne $target -or $after.Arguments -ne $arguments -or $after.WorkingDirectory -ne $workdir) {
        throw "Shortcut target changed unexpectedly: $path"
    }
    if ($after.IconLocation -notlike "*route-desk.ico*") {
        throw "Shortcut icon was not updated: $path ($($after.IconLocation))"
    }
    Write-Host "Updated shortcut: $path"
    Write-Host "  Target: $target"
    Write-Host "  Arguments: $arguments"
    Write-Host "  WorkingDirectory: $workdir"
    Write-Host "  Icon: $($after.IconLocation)"
    Write-Host "  AppUserModelID: $AppUserModelId"
    $updated++
}

Write-Host "Updated $updated shortcut(s). Routes, settings, and sign-in were not opened."
Write-Host "Quit Route Desk, then launch it again from the shortcut. If the taskbar pin still shows the Python icon, unpin that button and pin 'EVE Route Desk' again."

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public class AkaneForegroundWatcher {
  [DllImport("user32.dll")]
  public static extern IntPtr GetForegroundWindow();

  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowTextLength(IntPtr hWnd);

  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@

while ($true) {
  $handle = [AkaneForegroundWatcher]::GetForegroundWindow()
  $titleLength = [AkaneForegroundWatcher]::GetWindowTextLength($handle)
  $builder = New-Object System.Text.StringBuilder ([Math]::Max($titleLength + 1, 1))
  [void][AkaneForegroundWatcher]::GetWindowText($handle, $builder, $builder.Capacity)

  [uint32]$windowProcessId = 0
  [void][AkaneForegroundWatcher]::GetWindowThreadProcessId($handle, [ref]$windowProcessId)

  $processName = ""
  try {
    $processName = (Get-Process -Id $windowProcessId -ErrorAction Stop).ProcessName
  } catch {
    $processName = ""
  }

  $json = [pscustomobject]@{
    title = $builder.ToString().Trim()
    process_name = $processName
    pid = [int]$windowProcessId
    source = "foreground_watcher"
  } | ConvertTo-Json -Compress

  [Console]::Out.WriteLine($json)
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 700
}

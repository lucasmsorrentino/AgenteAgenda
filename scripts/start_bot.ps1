# start_bot.ps1 — Launcher for Windows Task Scheduler
# Ensures only one instance, sets working directory, logs output to file.
#
# Register with Task Scheduler:
#   schtasks /create /tn "ProductivityBot" /tr "powershell -ExecutionPolicy Bypass -File C:\Users\Lucas\Documents\automation\productivity\scripts\start_bot.ps1" /sc ONLOGON /rl HIGHEST
#
# Or run manually:
#   powershell -ExecutionPolicy Bypass -File scripts\start_bot.ps1

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogFile = Join-Path $ProjectRoot "data\bot_output.log"
$Python = "python"

# Ensure data directory exists
$DataDir = Join-Path $ProjectRoot "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

# Rotate log if > 5MB
if (Test-Path $LogFile) {
    $size = (Get-Item $LogFile).Length
    if ($size -gt 5MB) {
        $backup = "$LogFile.old"
        if (Test-Path $backup) { Remove-Item $backup -Force }
        Rename-Item $LogFile $backup
    }
}

# Start the bot (run_bot.py handles its own restart loop + lockfile)
Set-Location $ProjectRoot

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogFile -Value "[$timestamp] === Bot launcher started (PID $PID) ==="

# Run with output redirected to log
& $Python scripts/run_bot.py 2>&1 | ForEach-Object {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] $_"
} | Out-File -Append -FilePath $LogFile -Encoding utf8

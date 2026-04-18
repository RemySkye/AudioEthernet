param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [string]$ComputerName = "192.168.0.2",
    [string]$Username = "Administrator",
    [string]$PlainPassword,
    [securestring]$SecurePassword,
    [string]$RemoteProjectPath = "C:\Users\Administrator\Desktop\AudioEthernet"
)

if (-not (Get-Module -ListAvailable -Name Posh-SSH)) {
    Install-Module Posh-SSH -Scope CurrentUser -Force -AllowClobber
}

Import-Module Posh-SSH

if (-not $SecurePassword) {
    if ($PlainPassword) {
        $SecurePassword = ConvertTo-SecureString $PlainPassword -AsPlainText -Force
    }
    else {
        $SecurePassword = Read-Host "Password for $Username@$ComputerName" -AsSecureString
    }
}

$credential = [pscredential]::new($Username, $SecurePassword)
$session = New-SSHSession -ComputerName $ComputerName -Credential $credential -AcceptKey

try {
    switch ($Action) {
        "start" {
            Invoke-SSHCommand -SSHSession $session -Command "cmd /c taskkill /F /IM audioethernet.exe" | Out-Null
            $launcher = "$RemoteProjectPath\\start_sender_low.cmd"
            $remoteStart = "wmic process call create `"cmd /c $launcher`""
            Invoke-SSHCommand -SSHSession $session -Command $remoteStart | Out-Null
            $result = Invoke-SSHCommand -SSHSession $session -Command "cmd /c tasklist | findstr /I audioethernet"
            if ($result.Output) {
                $result.Output
            } else {
                "no-audioethernet-processes"
            }
        }
        "stop" {
            Invoke-SSHCommand -SSHSession $session -Command "cmd /c taskkill /F /IM audioethernet.exe" | Out-Null
            "stopped"
        }
        "status" {
            $result = Invoke-SSHCommand -SSHSession $session -Command "cmd /c tasklist | findstr /I audioethernet"
            if ($result.Output) {
                $result.Output
            } else {
                "no-audioethernet-processes"
            }
        }
    }
}
finally {
    Remove-SSHSession -SSHSession $session | Out-Null
}

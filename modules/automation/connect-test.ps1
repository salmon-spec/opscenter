param([ValidateRange(1024,65535)][int]$Port = 18089)
$ErrorActionPreference = 'Stop'
$sshDirectory = Join-Path $env:USERPROFILE '.ssh'
Write-Host "VM100 automation test: http://127.0.0.1:$Port/"
Write-Host 'Keep this terminal open. Close it to disconnect; remote services keep running.'
& ssh -F NUL -N -i (Join-Path $sshDirectory 'id_ed25519_hardened') `
    -o StrictHostKeyChecking=yes -o ('UserKnownHostsFile=' + (Join-Path $sshDirectory 'known_hosts')) `
    -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
    -L "127.0.0.1:${Port}:127.0.0.1:18089" iplus@10.66.66.6

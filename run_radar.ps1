$dir = "C:\Users\CONCRELAGOS\Dropbox\BIBLIOTECA JURIDICA - GERAL\CONCRELAGOS\12 - Equipe Jurídica\IGOR (ESTAGIÁRIO)\IGOR ESTAGIARIO JURÍDICO\LICITAÇÕES- PROJETO SITE"
$py  = "C:\Users\CONCRELAGOS\AppData\Local\Programs\Python\Python311\python.exe"
$log = Join-Path $dir "radar_log.txt"

Set-Location $dir
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "" -Encoding UTF8
Add-Content -Path $log -Value "=== $ts ===" -Encoding UTF8
& $py radar.py 2>&1 | Tee-Object -Append -FilePath $log

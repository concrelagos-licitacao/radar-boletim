@echo off
cd /d "C:\Users\CONCRELAGOS\Dropbox\BIBLIOTECA JURIDICA - GERAL\CONCRELAGOS\12 - Equipe Jurídica\IGOR (ESTAGIÁRIO)\IGOR ESTAGIARIO JURÍDICO\LICITAÇÕES- PROJETO SITE"
echo. >> radar_log.txt
echo === %date% %time% === >> radar_log.txt
"C:\Users\CONCRELAGOS\AppData\Local\Programs\Python\Python311\python.exe" radar.py >> radar_log.txt 2>&1

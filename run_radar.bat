@echo off
echo. >> radar_log.txt
echo === %date% %time% === >> radar_log.txt
"C:\Users\CONCRELAGOS\AppData\Local\Programs\Python\Python311\python.exe" radar.py >> radar_log.txt 2>&1

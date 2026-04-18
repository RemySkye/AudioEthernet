@echo off
cd /d C:\Users\Administrator\Desktop\AudioEthernet
.\.venv\Scripts\audioethernet -s --name lenovolap --bit-depth 16 --sample-rate 48000 --latency-profile low --frame-ms 5 --capture-processing unprocessed --log-level INFO

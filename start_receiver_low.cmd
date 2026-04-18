@echo off
cd /d C:\Users\Administrator\Desktop\AudioEthernet
.\.venv\Scripts\audioethernet -r --name ryzen-pc --bit-depth 16 --sample-rate 48000 --latency-profile low --frame-ms 5 --log-level INFO

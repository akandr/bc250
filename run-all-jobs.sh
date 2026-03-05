#!/bin/bash
set -euo pipefail
LOG=/tmp/run-all-jobs.log
echo "[$(date +%H:%M:%S)] Starting all jobs sequentially" | tee $LOG

echo "[$(date +%H:%M:%S)] Running event-scout..." | tee -a $LOG
timeout 300 python3 -u /opt/netscan/event-scout.py 2>&1 | tee -a $LOG
echo "[$(date +%H:%M:%S)] event-scout done" | tee -a $LOG

echo "[$(date +%H:%M:%S)] Running company-intel..." | tee -a $LOG
timeout 900 python3 -u /opt/netscan/company-intel.py 2>&1 | tee -a $LOG
echo "[$(date +%H:%M:%S)] company-intel done" | tee -a $LOG

echo "[$(date +%H:%M:%S)] Running career-scan --quick..." | tee -a $LOG
timeout 900 python3 -u /opt/netscan/career-scan.py --quick 2>&1 | tee -a $LOG
echo "[$(date +%H:%M:%S)] career-scan done" | tee -a $LOG

echo "[$(date +%H:%M:%S)] Running ha-correlate..." | tee -a $LOG
timeout 300 python3 -u /opt/netscan/ha-correlate.py 2>&1 | tee -a $LOG
echo "[$(date +%H:%M:%S)] ha-correlate done" | tee -a $LOG

echo "[$(date +%H:%M:%S)] ALL DONE" | tee -a $LOG

echo "=== Data file sizes ===" | tee -a $LOG
for f in /opt/netscan/data/events/latest-events.json /opt/netscan/data/intel/latest-intel.json /opt/netscan/data/career/latest-scan.json /opt/netscan/data/correlate/latest-correlate.json; do
    echo "$(wc -c < "$f") bytes  $f" | tee -a $LOG
done

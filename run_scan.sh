#!/bin/bash
# run_scan.sh — מריץ סריקה ושולח לוואטסאפ
cd /Users/iliaraizman/stock-analyzer
/Library/Developer/CommandLineTools/usr/bin/python3 scan_and_notify.py >> /Users/iliaraizman/stock-analyzer/scan.log 2>&1

"""
scan_and_notify.py — מריץ סריקה ושולח לוואטסאפ
נקרא על-ידי ה-cron בשעות: 11:00, 16:46, 18:45, 22:50
"""

import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from scanner import run_scan
from notify_email import send_scan_email

def main():
    now = datetime.datetime.now()
    scan_time = now.strftime('%H:%M')
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] מתחיל סריקה...")

    results = run_scan()
    print(f"סרקתי {len(results)} מניות")

    send_scan_email(results, scan_time)
    print("סיום.")

if __name__ == '__main__':
    main()

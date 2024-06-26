#!/usr/bin/env python3

import datetime
import os
import subprocess
import sys
import time

import croniter

if os.environ.get("PREVENT_EXECUTION") == "1":
    print("cron.py: Doing nothing forever due to environment variable")
    while True:
        time.sleep(3600)

if os.environ.get("FORCE_SINGLE_EXECUTION") == "1":
    print("cron.py: Forcing single execution due to environment variable")
    subprocess.run(["./gnu_elpa_mirror.py"])
    print(
        "cron.py: Not running again, please unset FORCE_SINGLE_EXECUTION to resume scheduling"
    )
    while True:
        time.sleep(3600)

# Every day at 12am UTC (or system timezone)
for next_time in croniter.croniter(
    "0 0 * * *", datetime.datetime.now(), ret_type=datetime.datetime
):
    if datetime.datetime.now() > next_time:
        # Last invocation took so long we passed the next time to
        # invoke (or more than one), wait until the next scheduled
        # time in that case.
        continue
    print("cron.py: Next invocation of job is at:", next_time)
    while datetime.datetime.now() < next_time:
        time.sleep(60)
    # Ignore errors.
    subprocess.run(["./gnu_elpa_mirror.py"])
    # Work around an issue that I'm seeing on Railway where memory
    # utilization doesn't go away even after finishing the run.
    if os.environ.get("CRON_FORCE_CONTAINER_RESTART") == "1":
        print("cron.py: Forcing container restart to workaround memory leak")
        sys.exit(1)
    if os.environ.get("CRON_FORCE_CACHE_EVICTION") == "1":
        print("cron.py: Forcing cache eviction")
        subprocess.run(["vmtouch", "-e", "repos"])
        print("cron.py: Cache eviction completed")

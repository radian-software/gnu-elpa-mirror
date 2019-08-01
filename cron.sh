#!/usr/bin/env bash

set -e
set -o pipefail

time="$(date +%I:%M)"

if [[ "$1" != "-f" ]] && ! (
           [[ "$time" > "02:30" && "$time" < "03:30" ]] ||
               [[ "$time" > "05:30" && "$time" < "06:30" ]] ||
               [[ "$time" > "08:30" && "$time" < "09:30" ]]); then
    echo "exiting as it is not a scheduled time; pass -f to override" 1>&2
    exit 1
fi

python3 -u -m gnu_elpa_mirror

#!/usr/bin/env bash

set -e
set -o pipefail

time="$(date +%I:%M)"

if [[ "$1" != "-f" ]] && ! [[ "$time" > "05:30" && "$time" < "06:30" ]]; then
    echo "exiting as it is not 6am or 6pm; pass -f to override" 1>&2
    exit 1
fi

python3 -u -m gnu_elpa_mirror

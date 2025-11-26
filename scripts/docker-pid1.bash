#!/usr/bin/env bash

set -euo pipefail

# It would be attractive to simply add the created user account to the
# sudo group, however configuring a second NOPASSWD specification for
# the sudo group (in addition to the default one that doesn't use
# NOPASSWD) causes the second one to be ignored.

cat <<"EOF" > /etc/sudoers.d/gnu-elpa-mirror
gnu-elpa-mirror ALL=(ALL:ALL) NOPASSWD: ALL
EOF

groupadd -g "$(stat -c %g "$PWD")" -o -p '!' -r gnu-elpa-mirror
useradd -u "$(stat -c %u "$PWD")" -g "$(stat -c %g "$PWD")" \
        -o -p '!' -m -N -s /usr/bin/bash gnu-elpa-mirror

runuser -u gnu-elpa-mirror touch /home/gnu-elpa-mirror/.sudo_as_admin_successful

if (( "$#" == 0 )) || [[ -z "$1" ]]; then
    set -- bash
fi

if (( "$#" == 1 )) && [[ "$1" == *" "* ]]; then
    set -- bash -c "$1"
fi

exec runuser -u gnu-elpa-mirror -- "$@"

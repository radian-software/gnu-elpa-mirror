#!/bin/sh

mkdir -p /tmp/emacs-bin
ln -sf "$(which emacs25)" /tmp/emacs-bin/emacs
export PATH="/tmp/emacs-bin:$PATH"
python3 -m gnu_elpa_mirror

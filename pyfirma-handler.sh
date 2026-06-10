#!/bin/bash
cd "$(dirname "$0")"

# We log the invocation to /tmp to debug if the snap is actually calling it 
echo "[$(date)] PyFirma Invoked with arg: $1" >> /tmp/pyfirma_launch.log

# Launch pyfirma
./venv/bin/python main.py "$1" >> /tmp/pyfirma_launch.log 2>&1

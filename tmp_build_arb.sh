#!/bin/bash
set -e
cd /tmp
python3 -c "import zipfile; z=zipfile.ZipFile('/tmp/arb-engine.zip'); z.extractall('/tmp/arb-engine-src')"
cd /tmp/arb-engine-src/arb-engine
sudo docker build -t hydra-arb:latest .
echo "Build complete"

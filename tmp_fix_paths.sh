#!/bin/bash
cd /tmp/arb-engine-src
# Create proper directories and move files
mkdir -p arb-engine/cmd/arb
mkdir -p arb-engine/internal/bridge
mkdir -p arb-engine/internal/engine
mkdir -p arb-engine/internal/exchange
mkdir -p arb-engine/internal/metrics
mkdir -p arb-engine/internal/ringbuf
mkdir -p arb-engine/internal/strategy

# Move files from backslash names to proper paths
for f in arb-engine\*; do
    target=$(echo "$f" | sed 's/\\/\//g')
    mv "$f" "$target" 2>/dev/null || true
done

echo "Paths fixed"
ls -la arb-engine/

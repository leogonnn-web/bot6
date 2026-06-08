import zipfile, os, shutil, subprocess

# Clean and extract
shutil.rmtree('/tmp/arb-engine', ignore_errors=True)
os.makedirs('/tmp/arb-engine', exist_ok=True)

z = zipfile.ZipFile('/tmp/arb-engine.zip')
for n in z.namelist():
    target = os.path.join('/tmp/arb-engine', n)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'wb') as f:
        f.write(z.read(n))

print('Extracted files:')
for f in sorted(os.listdir('/tmp/arb-engine')):
    print(f)

# Build Docker
os.chdir('/tmp/arb-engine/arb-engine')
result = subprocess.run(['sudo', 'docker', 'build', '-t', 'hydra-arb:latest', '.'], capture_output=True, text=True)
print(result.stdout)
if result.returncode != 0:
    print('BUILD ERROR:', result.stderr)
    exit(1)

print('Build complete')

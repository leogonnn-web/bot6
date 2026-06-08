import os, shutil

src = '/tmp/arb-engine-src'
for root, dirs, files in os.walk(src):
    for f in files:
        if '\\' in f:
            old_path = os.path.join(root, f)
            new_rel = f.replace('\\', '/')
            new_path = os.path.join(root, new_rel)
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)
    for d in dirs[:]:  
        if '\\' in d:
            old_path = os.path.join(root, d)
            new_rel = d.replace('\\', '/')
            new_path = os.path.join(root, new_rel)
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if os.path.exists(new_path):
                for item in os.listdir(old_path):
                    shutil.move(os.path.join(old_path, item), os.path.join(new_path, item))
                os.rmdir(old_path)
            else:
                shutil.move(old_path, new_path)
            dirs.remove(d)

print('Done')
for f in sorted(os.listdir(os.path.join(src, 'arb-engine'))):
    print(f)

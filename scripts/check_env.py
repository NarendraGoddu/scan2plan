import importlib

mods = ['torch', 'torchvision', 'cv2', 'scipy', 'PIL', 'numpy',
        'skimage', 'open3d', 'trimesh', 'onnxruntime', 'matplotlib']
for m in mods:
    try:
        mod = importlib.import_module(m)
        print('%-14s OK       %s' % (m, getattr(mod, '__version__', '?')))
    except Exception:
        print('%-14s MISSING' % m)

print()
print('requirements.txt:')
with open('requirements.txt', encoding='utf-8') as fh:
    print(fh.read())

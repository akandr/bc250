#!/usr/bin/env python3
"""Run the A4 GGUF tensor-table accounting over every model on the board:
each Ollama-served model (resolved from the manifest store) and every GGUF in
/opt/models.  Metadata only, no GPU.  Run as root (the Ollama store is root-only)."""
import json, os, glob, sys, platform, datetime, importlib.util
spec = importlib.util.spec_from_file_location('a4', '/tmp/bench-a4-gguf-bytes.py')
a4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(a4)
base = '/usr/share/ollama/.ollama/models'
models = []
for mf in sorted(glob.glob(base + '/manifests/*/*/*/*')):
    parts = mf.split('/'); tag, name, ns = parts[-1], parts[-2], parts[-3]
    label = f"{name}:{tag}" if ns == 'library' else f"{ns}/{name}:{tag}"
    try:
        m = json.load(open(mf))
    except Exception as e:
        models.append({'label': label, 'manifest': mf, 'error': f'manifest: {e}'}); continue
    for layer in m.get('layers', []):
        if layer.get('mediaType', '').endswith('.model'):
            path = base + '/blobs/' + layer['digest'].replace(':', '-')
            try:
                r = a4.main(path, 'ollama:' + label); r['ollama_tag'] = label; r['source'] = 'ollama-store'
                models.append(r)
            except Exception as e:
                models.append({'label': label, 'path': path, 'error': str(e)})
for g in sorted(glob.glob('/opt/models/*.gguf')):
    try:
        r = a4.main(g, 'file:' + os.path.basename(g)); r['source'] = '/opt/models'; models.append(r)
    except Exception as e:
        models.append({'label': g, 'error': str(e)})
out = {'host': platform.node(), 'kernel': platform.release(),
       'date': datetime.datetime.now().isoformat(timespec='seconds'),
       'harness': 'bench-a4-gguf-bytes.py (main() per file)', 'models': models}
print(json.dumps(out, indent=1))

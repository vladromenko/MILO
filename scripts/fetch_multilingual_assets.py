"""Download pinned upstream speech/vision assets on an internet-connected Mac."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'multilingual-assets'
VOICES = ('fr_FR-siwis-low', 'de_DE-thorsten-low', 'ja_JP-hi_fi_captain-medium',
          'ar_JO-kareem-low', 'ur_PK-aegis_female-medium', 'ru_RU-denis-medium')


def metadata(repo):
    with urllib.request.urlopen('https://huggingface.co/api/models/' + repo + '?blobs=true', timeout=60) as r:
        return json.load(r)


def fetch(url, path, expected=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open('wb') as dest:
            while chunk := source.read(1024 * 1024):
                dest.write(chunk)
        temporary.replace(path)
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if expected and digest != expected:
        raise RuntimeError('Checksum mismatch: ' + str(path))
    print(path.name, path.stat().st_size, 'verified', flush=True)
    return {'path': str(path.relative_to(OUT)), 'url': url, 'sha256': digest}


def main():
    manifest = []
    repo = 'rhasspy/piper-voices'
    info = metadata(repo)
    revision = info['sha']
    for item in info['siblings']:
        name = item['rfilename']
        if any(name.endswith(v + ext) for v in VOICES for ext in ('.onnx', '.onnx.json')):
            manifest.append(fetch(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',
                OUT / 'edge/tts' / Path(name).name, item.get('lfs', {}).get('sha256')))
    repo = 'ggml-org/SmolVLM2-500M-Video-Instruct-GGUF'
    info = metadata(repo)
    for item in info['siblings']:
        name = item['rfilename']
        if name.endswith('Q8_0.gguf'):
            manifest.append(fetch(f'https://huggingface.co/{repo}/resolve/{info["sha"]}/{name}',
                OUT / 'edge/vlm/smolvlm' / name, item['lfs']['sha256']))
    repo = 'ggerganov/whisper.cpp'
    info = metadata(repo)
    item = next(x for x in info['siblings'] if x['rfilename'] == 'ggml-base.bin')
    manifest.append(fetch(f'https://huggingface.co/{repo}/resolve/{info["sha"]}/ggml-base.bin',
        OUT / 'brain/whisper/ggml-base.bin', item['lfs']['sha256']))
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()

"""Offline voice synthesis smoke test. Writes WAVs; never plays audio or moves hardware."""
import json
import argparse
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.languages import LANGUAGES

PHRASES = {'en': 'Hello, how are you today?', 'ru': 'Привет, как у тебя дела?',
    'fr': 'Bonjour, comment allez-vous ?', 'de': 'Hallo, wie geht es dir?',
    'ja': 'こんにちは。お元気ですか。', 'ar': 'مرحبا، كيف حالك اليوم؟', 'ur': 'السلام علیکم، آپ کیسے ہیں؟'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stt', action='store_true', help='also transcribe generated WAVs; no microphone test')
    args = parser.parse_args()
    from piper import PiperVoice
    out = ROOT / 'data/language-check'
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for code, (_, name) in LANGUAGES.items():
        try:
            start = time.monotonic()
            voice = PiperVoice.load(ROOT / 'assets/models/tts' / (name + '.onnx'))
            load_s = time.monotonic() - start
            start = time.monotonic()
            with wave.open(str(out / (code + '.wav')), 'wb') as wav:
                voice.synthesize_wav(PHRASES[code], wav)
            elapsed = time.monotonic() - start
            with wave.open(str(out / (code + '.wav')), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
                assert duration > .1
            print(json.dumps({'language': code, 'load_s': round(load_s, 2),
                'synthesis_s': round(elapsed, 2), 'audio_s': round(duration, 2)}), flush=True)
            if args.stt:
                import subprocess
                start = time.monotonic()
                result = subprocess.run(['curl', '--fail-with-body', '--max-time', '30', '-sS',
                    'http://127.0.0.1:8873/inference', '-F', 'file=@' + str(out / (code + '.wav')),
                    '-F', 'response_format=json', '-F', 'language=' + code],
                    check=True, capture_output=True, text=True)
                text = json.loads(result.stdout)['text'].strip()
                if not text:
                    raise ValueError('empty transcription')
                print(json.dumps({'language': code, 'stt_s': round(time.monotonic()-start, 2),
                    'transcription': text}, ensure_ascii=False), flush=True)
        except Exception as exc:
            failed.append(code)
            print(json.dumps({'language': code, 'error': str(exc)}), flush=True)
    raise SystemExit(bool(failed))


if __name__ == '__main__':
    main()

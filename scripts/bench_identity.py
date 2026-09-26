"""Anonymous live-face continuity test in a disposable database. No enrollment.

Never prints or retains photos/embeddings; leaves production memory untouched.
Requires exactly one consenting subject in view. Does not identify that person.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.memory import MemoryStore
from milo_next.settings import settings
from milo_next.brain import MODEL_ID


async def main():
    samples = []
    track = None
    headers = {"Authorization": "Bearer " + settings()["token"]}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as client:
        for _ in range(10):
            async with client.get("http://127.0.0.1:8872/perception") as response:
                response.raise_for_status()
                p = await response.json()
            faces = p.get("faces", [])
            if len(faces) != 1 or not faces[0].get("embedding"):
                raise RuntimeError("one visible face with a valid embedding is required")
            face = faces[0]
            if track is not None and face["track_id"] != track:
                raise RuntimeError("face track changed; refusing to combine subjects")
            track = face["track_id"]
            samples.append(face["embedding"])
            await asyncio.sleep(.7)
    with tempfile.TemporaryDirectory(prefix="milo-anonymous-face-test-") as directory:
        path = Path(directory) / "test.sqlite3"
        with MemoryStore(path) as memory:
            person = memory.create_person("anonymous commissioning sample")
            memory.add_face_embedding(person, samples[0], MODEL_ID)
            memory.put_fact(person, "test_marker", "commissioning-only-marker")
        with MemoryStore(path) as memory:
            matches = [memory.match_face(v, MODEL_ID, threshold=.55) for v in samples[1:]]
            session = memory.new_session(person)
            restored = "commissioning-only-marker" in memory.context(session, "test_marker")
            unknown = "commissioning-only-marker" not in memory.context(memory.new_session(), "test_marker")
        print(json.dumps({"samples": len(samples), "matched": sum(m.person_id == person for m in matches),
                          "cosine_scores": [round(m.score, 3) for m in matches],
                          "memory_restored": restored, "anonymous_isolation": unknown,
                          "production_memory_modified": False}))


asyncio.run(main())

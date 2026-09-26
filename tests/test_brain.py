import asyncio
import json
import time
import pytest
from dataclasses import asdict

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from milo_next import brain
from milo_next.http import authentication


def test_tracking_requires_one_face_at_safe_distance():
    assert brain.tracking_center([{"bbox": [.2, .3, .2, .2]}]) == pytest.approx((.3, .4))
    for faces in ([], [{"bbox": [.1, .1, .2, .2]}] * 2,
                  [{"bbox": [.1, .1, .7, .2]}], [{"bbox": [.1, .1, .2, .85]}],
                  [{"bbox": [.9, .1, .2, .2]}], [{"bbox": [float("nan"), .1, .2, .2]}],
                  [{"bbox": [True, .1, .2, .2]}], [{}]):
        assert brain.tracking_center(faces) is None


def test_short_first_sentence_is_spoken_before_later_tokens(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    spoken = []

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def json(self):
            return {"tokens": []}

        @property
        def content(self):
            async def tokens():
                for part in ("Hello.", " How are you?"):
                    if part.startswith(" "):
                        assert spoken == ["Hello."]
                    yield b"data: " + json.dumps({"choices": [{"delta": {"content": part}}]}).encode() + b"\n"
            return tokens()

    class HTTP:
        def post(self, *args, **kwargs):
            if args[0].endswith('/v1/chat/completions'):
                messages = kwargs['json']['messages']
                assert 'AI robot companion' in messages[0]['content']
                assert 'Only introduce yourself' in messages[0]['content']
                assert 'do not repeat your name' in messages[0]['content']
                assert messages[-1]['content'].endswith('CURRENT REQUEST (answer this):\nHi')
            return Response()

    async def control(**body):
        if "say" in body:
            spoken.append(body["say"])

    app.http, app.control = HTTP(), control
    try:
        assert asyncio.run(app.answer("Hi", app.session_id)) == "Hello. How are you?"
        assert spoken == ["Hello.", "How are you?"]
        assert app.metrics['reply_source'] == 'llm'
    finally:
        app.memory.close()

def make_brain(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(brain, "ROOT", tmp_path)
    return brain.Brain({"token": "test-secret", "face_threshold": .9})


def test_current_view_captures_once_without_translation_or_arm_scan(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    calls = []
    async def edge_visual(**body): calls.append(body)
    async def finish(question): calls.append({'question': question})
    def scan(*args): pytest.fail('A current view question must not scan')
    app.edge_visual, app.finish_visual = edge_visual, finish
    app.object_search.start = scan
    # No HTTP client: English current-view questions need no preliminary LLM call.
    try:
        asyncio.run(app.prepare_visual('What do you see?'))
        assert calls == [{'action': 'clear'}, {'action': 'capture'}, {'question': 'What do you see?'}]
        assert app.actuator.status()['published_count'] == 0
    finally:
        app.memory.close()


def test_general_scene_answer_does_not_inject_phone_use_topic(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    async def edge_visual(**body):
        return {'description': 'A person near a table.', 'views': 1,
                'detector_evidence': [{'people_detected': 1, 'phones_detected': 0,
                                      'people_near_phone_candidates': 0, 'note': 'phone warning'}]}
    app.edge_visual = edge_visual
    try:
        asyncio.run(app.finish_visual('What do you see?'))
        prompt = app.queue.get_nowait()[0]
        assert 'phone' not in prompt.lower()
        assert 'What do you see?' in prompt
        assert 'A person near a table.' in prompt
    finally:
        app.memory.close()


@pytest.mark.parametrize('requested', [False, True])
def test_edge_restart_preserves_only_existing_tracking_permission(tmp_path, monkeypatch, requested):
    app = make_brain(tmp_path, monkeypatch)
    app.boot, app.tracking_requested = 'before', requested
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def json(self):
            return {'boot':'after', 'perception':{'frame_seq':1,'capture_age_ms':0,'faces':[]}}
    class HTTP:
        def get(self, *_): return Response()
    async def control(**_): pass
    async def stop(_): raise asyncio.CancelledError
    app.http, app.control = HTTP(), control
    monkeypatch.setattr(brain.asyncio, 'sleep', stop)
    try:
        with pytest.raises(asyncio.CancelledError): asyncio.run(app.poll())
        assert app.error is None
        assert app.tracking_requested is requested
        assert app.actuator.status()['state'] == 'DISARMED'
        assert app.actuator.status()['published_count'] == 0
    finally:
        app.memory.close()


@pytest.mark.parametrize('label', ['happy','sad','angry','fearful','surprised'])
def test_stable_expression_queues_a_contextual_llm_request(tmp_path, monkeypatch, label):
    app = make_brain(tmp_path, monkeypatch)
    app.model_ready = True
    app.last_checkin = time.monotonic() - 100
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def json(self):
            return {'boot':'one', 'perception':{'frame_seq':1,'capture_age_ms':0,
                'faces':[{'track_id':1,'bbox':[.2,.2,.2,.3],'engaged':.8,
                    'expression':{'label':label,'model_score':.8,'stable_samples':4}}]},
                'audio':{'state':'listening','speaking':False}}
    class HTTP:
        def get(self, *_): return Response()
    async def control(**_): pass
    async def stop(_): raise asyncio.CancelledError
    app.http, app.control = HTTP(), control
    monkeypatch.setattr(brain.asyncio, 'sleep', stop)
    try:
        with pytest.raises(asyncio.CancelledError): asyncio.run(app.poll())
        assert app.error is None
        prompt, _, _, proactive, _ = app.queue.get_nowait()
        assert label in prompt and 'uncertain' in prompt and proactive
        assert app.metrics['last_expression_reaction']['source'] == 'llm'
    finally:
        app.memory.close()


def test_voice_memory_enroll_recall_and_confirmed_forget(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        p = {'faces': [{'track_id': 1, 'embedding': [1, 0]}]}
        app.last_new_frame = time.monotonic()
        for _ in range(3):
            app.samples.append((time.monotonic(), p))
            app.update_identity(p)
        scope = app.session_key
        assert 'remember you as Alice' in app.memory_reply('Remember me as Alice', scope)
        for _ in range(3):
            app.update_identity(p)
        assert app.identity['name'] == 'Alice'
        scope = app.session_key
        assert 'saved' in app.memory_reply('Milo, remember that I like green tea', scope)
        person = app.identity['person_id']
        other_session = app.memory.new_session(person)
        assert 'green tea' in app.memory.context(other_session, 'tea')
        assert 'green tea' in app.memory.context(other_session, 'What do you remember about me?')
        assert 'green tea' not in app.memory.context(app.memory.new_session(), 'What do you remember about me?')
        assert 'no matching' in app.memory_reply('Yes, forget my memories', scope)
        assert 'confirm' in app.memory_reply('Forget everything about me', scope)
        assert 'deleted' in app.memory_reply('Yes, forget my memories', scope)
        assert not app.memory.match_face([1, 0], brain.MODEL_ID).known
    finally:
        app.memory.close()


def test_voice_memory_does_not_attribute_unknown_or_changed_speaker(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        assert 'recognize you first' in app.memory_reply('Remember that I like tea', None)
        assert 'repeat' in app.memory_reply('Remember me as Alice', ('old-track',))
        assert 'could not safely' in app.memory_reply('Remember me as Alice', None)
    finally:
        app.memory.close()


def test_remembering_visual_words_saves_person_fact_without_starting_scan(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    spoken = []
    async def control(**body):
        spoken.append(body)
    app.control = control
    try:
        person = app.memory.create_person('Alice')
        app.identity = {'person_id': person, 'name': 'Alice'}
        app.last_new_frame = time.monotonic()
        reply = asyncio.run(app.answer('Remember that I like to look around museums', app.session_id,
                                       expected_scope=app.session_key))
        assert 'saved' in reply
        assert app.visual_task is None and not app.object_search.active
        assert app.actuator.status()['published_count'] == 0
        known_session = app.memory.new_session(person)
        assert 'museums' in app.memory.context(known_session, 'What do you remember about me?')
        assert 'museums' not in app.memory.context(app.memory.new_session(), 'What do you remember about me?')
        assert spoken[0]['say'] == reply
    finally:
        app.memory.close()


def test_identity_requires_three_frames_and_isolates_unknown(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        person = app.memory.create_person("Alice")
        app.memory.add_face_embedding(person, [1, 0], brain.MODEL_ID)
        p = {"faces": [{"track_id": 1, "embedding": [1, 0]}]}
        app.update_identity(p)
        assert app.identity is None
        app.update_identity(p)
        assert app.identity is None
        app.update_identity(p)
        assert app.identity["person_id"] == person
        known = app.speech_session()
        app.memory.put_fact(person, "preference", "green tea")
        assert "green tea" in app.memory.context(known, "tea")
        app.lose_identity()
        assert app.session_id != known
        app.speech_session()
        assert "green tea" not in app.memory.context(app.session_id, "tea")
        disconnected = app.session_id
        app.lose_identity()
        assert app.session_id == disconnected
    finally:
        app.memory.close()


def test_multiple_people_never_assign_speaker(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        p = {"faces": [{"track_id": 1, "embedding": [1, 0]}, {"track_id": 2, "embedding": [0, 1]}]}
        for _ in range(4):
            app.update_identity(p)
        assert app.identity is None
    finally:
        app.memory.close()


def test_camera_motion_does_not_create_empty_sessions(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        for track in range(100):
            app.update_identity({"faces": [{"track_id": track}]})
        assert app.memory._db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        app.speech_session()
        assert app.memory._db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
    finally:
        app.memory.close()


def test_unauthenticated_requests_cannot_control():
    async def run():
        app = web.Application(middlewares=[authentication("secret")])
        async def command(_):
            return web.json_response({"ok": True})
        app.router.add_post("/command", command)
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/command")).status == 401
            assert (await client.post("/command", headers={"Authorization": "Bearer wrong"})).status == 401
            assert (await client.post("/command", headers={"Authorization": "Bearer secret"})).status == 200
    asyncio.run(run())


def test_arming_refused_without_fresh_camera(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    class Request:
        async def json(self):
            return {"action": "arm"}
    try:
        import pytest
        with pytest.raises(ValueError, match="fresh edge camera"):
            asyncio.run(app.command(Request()))
        assert app.actuator.gate.status().published_count == 0
    finally:
        app.memory.close()


def test_emotion_is_uncertain_not_inferred_from_faces():
    assert brain.social_signal("Hello", [{"bbox": [.1, .1, .2, .2]}])["facial_emotion"] == "unknown"
    assert brain.social_signal("I feel sad", [])["self_report"] == "distress"


def test_no_automatic_movement_in_constructor(tmp_path, monkeypatch):
    app = make_brain(tmp_path, monkeypatch)
    try:
        status = app.actuator.gate.status()
        assert status.state == "DISARMED"
        assert status.published_count == 0
    finally:
        app.memory.close()


@pytest.mark.parametrize("recognizer_available", [True, False])
def test_health_reports_recognizer_separately(tmp_path, monkeypatch, recognizer_available):
    app = make_brain(tmp_path, monkeypatch)

    class HealthyResponse:
        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def json(self):
            return {"status": "ok"}

    class HTTP:
        def get(self, url):
            if ":8783/" in url and not recognizer_available:
                raise OSError("recognizer offline")
            return HealthyResponse()

        def post(self, url, **kwargs):
            assert kwargs['json']['max_tokens'] == 1
            assert kwargs['headers']['Connection'] == 'close'
            return HealthyResponse()

    async def finish(_):
        assert app.model_ready is True
        assert app.stt_ready is recognizer_available
        raise asyncio.CancelledError

    app.http = HTTP()
    monkeypatch.setattr(brain.asyncio, "sleep", finish)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app.model_health())
    finally:
        app.memory.close()

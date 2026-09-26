import asyncio
import time
from types import SimpleNamespace

import pytest

from milo_next.visual_intents import visual_request, sweep_requested, simple_search_request
from milo_next.object_search import search_intent
from milo_next.memory_intents import parse_memory_intent
from milo_next.visual_observer import VisualObserver, detector_evidence


@pytest.mark.parametrize('question', [
    'Посмотри вокруг и скажи сколько людей сидит с телефонами',
    'Как выглядит человек передо мной?', 'Find my keys',
    'Describe the person in front of me', 'Regarde autour de toi',
    'Wie viele Personen sind hier?', '周りを見回してください', 'انظر حولك', 'آس پاس دیکھو'])
def test_free_form_scene_questions(question):
    assert visual_request(question)


@pytest.mark.parametrize('question', ['Who are you?', 'Кто ты?', 'How are you?', 'Мне грустно', 'Tell me a story'])
def test_ordinary_dialogue_does_not_invoke_vision(question):
    assert not visual_request(question)


def test_motion_requires_explicit_sweep_and_respects_negation():
    assert sweep_requested('Посмотри вокруг и скажи сколько людей')
    assert not sweep_requested('Не осмотрись, просто опиши кадр')
    assert not sweep_requested('Describe the person in front of me')
    assert not sweep_requested('Explain how to look around')
    assert not sweep_requested('Я сказал осмотрись, но сейчас просто поговорим')
    assert sweep_requested('Could you look around and describe the room?')


@pytest.mark.parametrize('question', [
    'What do you see?', 'What can you see?', 'What are you looking at?',
    'What is in front of you?', 'Что ты видишь?', 'Скажи, что видишь перед собой',
])
def test_current_scene_question_never_requests_a_sweep(question):
    assert visual_request(question)
    assert not sweep_requested(question)
    assert search_intent(question) is None


@pytest.mark.parametrize('question', [
    'сколько человек вокруг сидит с телефоном',
    'How many people around me are sitting with phones?',
    'найди человека с телефоном', 'find a person wearing red',
])
def test_around_counts_and_qualified_search_request_horizontal_sweep(question):
    assert visual_request(question) and sweep_requested(question)


@pytest.mark.parametrize('question', [
    'сколько человек ты видишь перед собой', 'How many people do you see in front of you?',
    'Do not look around, count people in this view',
    'Сколько людей вокруг, но без поворота камеры?',
    'Can you explain how to find a person with a phone?',
])
def test_front_view_and_negated_or_quoted_requests_do_not_sweep(question):
    assert not sweep_requested(question)


def test_qualified_search_is_not_reduced_to_a_single_object_label():
    assert simple_search_request('find my phone', search_intent('find my phone'))
    for text in ('find a person wearing red', 'look around and describe the person',
                 'найди человека с телефоном'):
        assert not simple_search_request(text, search_intent(text))


def test_phone_association_is_not_invented_or_double_counted():
    person = {'label': 'person', 'score': .9, 'bbox': [.1, .1, .7, .8]}
    phone = {'label': 'cell phone', 'score': .9, 'bbox': [.2, .2, .1, .1]}
    def perception(objects):
        return {'objects': objects, 'captured_monotonic': 10,
                'timings': {'objects_captured_monotonic': 9.8},
                'status': {'objects': {'state': 'running'}}}
    assert detector_evidence(perception([person]))['people_near_phone_candidates'] == 0
    result = detector_evidence(perception([person, phone, phone]))
    assert result['people_near_phone_candidates'] == 1
    assert result['people_detected'] == 1 and result['phones_detected'] == 2
    assert detector_evidence(perception([person, person, phone]))['people_near_phone_candidates'] == 0


@pytest.mark.parametrize('state,age', [('error', .1), ('running', 2), ('running', float('nan'))])
def test_unavailable_detector_is_not_a_zero_count(state, age):
    result = detector_evidence({'captured_monotonic': 10,
        'timings': {'objects_captured_monotonic': 10 - age},
        'status': {'objects': {'state': state}}, 'faces': [{}],
        'objects': [{'label': 'person', 'score': .9, 'bbox': [0, 0, 1, 1]}]})
    assert result['people_detected'] is None and result['phones_detected'] is None
    assert result['faces'] == 1 and not result['objects']
    assert not result['object_detector_available']


def test_visual_frames_expire_and_busy_requests_do_not_consume_frames():
    async def run():
        observer = VisualObserver(SimpleNamespace())
        for _ in range(10):
            observer.frames.append((time.monotonic()-151, 'jpeg', {}))
        assert len(observer.frames) == 3
        async with observer.lock:
            with pytest.raises(ValueError, match='busy'):
                await observer.ask('Describe the scene')
            assert len(observer.frames) == 3
        with pytest.raises(ValueError, match='expired'):
            await observer.ask('Describe the scene')
        assert not observer.frames
        with pytest.raises(ValueError, match='invalid'):
            await observer.ask('')
    asyncio.run(run())


@pytest.mark.parametrize('question', ['How many people are sitting with phones?', 'What do you see?'])
def test_visual_question_follows_images_and_unknown_counts_are_preserved(monkeypatch, question):
    calls = []
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def raise_for_status(self): pass
        async def json(self): return {'choices': [{'message': {'content': 'The count is uncertain.'}}]}
    class HTTP:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs['json']))
            return Response()
    monkeypatch.setattr('milo_next.visual_observer.aiohttp.ClientSession', HTTP)
    async def run():
        observer = VisualObserver(SimpleNamespace())
        observer.frames.append((time.monotonic(), 'jpeg', detector_evidence({})))
        result = await observer.ask(question)
        assert result['approximate'] and result['views'] == 1
        content = calls[0][1]['messages'][0]['content']
        assert content[0]['type'] == 'image_url'
        assert content[-1]['text'].startswith(question)
        if question.startswith('How many'):
            assert '"people_detected": null' in content[-1]['text']
        else:
            assert 'phone' not in content[-1]['text'].lower()
            assert 'not a general scene description' not in content[-1]['text']
        assert not observer.frames
    asyncio.run(run())


@pytest.mark.parametrize('language,text,name', [
    ('ru', 'запомни меня как Влад', 'Влад'),
    ('fr', 'souviens-toi de moi comme Alice', 'Alice'),
    ('de', 'merke dir meinen namen als Anna', 'Anna'),
    ('ja', '私を太郎として覚えて', '太郎'),
    ('ar', 'تذكرني باسم عمر', 'عمر'),
    ('ur', 'مجھے علی کے نام سے یاد رکھو', 'علی')])
def test_explicit_memory_commands_use_selected_language(language, text, name):
    assert parse_memory_intent(text, language) == {'action': 'enroll', 'name': name}
    assert parse_memory_intent(text, 'en') is None

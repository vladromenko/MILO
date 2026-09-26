from milo_next.scene_reactions import SceneReactions


def frame(seq, score=.9, label='cup'):
    return {'captured_monotonic': seq, 'timings': {
        'objects_frame_seq': seq, 'objects_captured_monotonic': seq},
        'status': {'backends': {'objects': 'hailort_h10'}},
        'objects': [{'label': label, 'score': score}]}


def test_stable_observation_generates_event_not_canned_speech():
    now = [100]
    scene = SceneReactions(lambda: now[0])
    kwargs = dict(mode='active', proactivity='normal', present=True, idle=True, last_utterance=0)
    for seq in range(1, 25):
        now[0] += .7
        scene.observe(frame(seq), boot='a', fresh=True)
    event = scene.take(**kwargs)
    assert event == {'label': 'cup', 'confidence': .9}
    assert 'Generate' in scene.prompt(event)
    assert 'what a cup contains' in scene.prompt(event)
    assert scene.take(**kwargs) is None
    now[0] += 100
    for seq in range(25, 29):
        now[0] += .7
        scene.observe(frame(seq), boot='a', fresh=True)
    assert scene.take(**kwargs) is None


def test_low_confidence_stale_repeated_frames_and_absence_never_trigger():
    now = [100]
    scene = SceneReactions(lambda: now[0])
    kwargs = dict(mode='active', proactivity='normal', present=True, idle=True, last_utterance=0)
    for _ in range(30):
        now[0] += .7
        scene.observe(frame(1), boot='a', fresh=True)
    assert scene.take(**kwargs) is None
    for seq in range(2, 32):
        now[0] += .7
        scene.observe(frame(seq, .5), boot='a', fresh=True)
    assert scene.take(**kwargs) is None
    scene.observe(frame(33), boot='a', fresh=False)
    assert not scene.seen


def test_quiet_busy_absent_and_recent_speech_suppress():
    now = [100]
    scene = SceneReactions(lambda: now[0])
    for seq in range(1, 30):
        now[0] += .7
        scene.observe(frame(seq), boot='a', fresh=True)
    kwargs = dict(mode='active', proactivity='normal', present=True, idle=True, last_utterance=0)
    for change in ({'mode':'sleep'}, {'proactivity':'quiet'}, {'present':False},
                   {'idle':False}, {'last_utterance':now[0]}):
        assert scene.take(**(kwargs | change)) is None
    assert scene.take(**kwargs)


def test_hailo_failure_discards_last_objects():
    scene = SceneReactions()
    scene.observe(frame(1), boot='a', fresh=True)
    failed = frame(2)
    failed['status']['backends']['objects'] = 'unavailable'
    scene.observe(failed, boot='a', fresh=True)
    assert scene.seen == {}

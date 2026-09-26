import pytest
from milo_next.expression import ExpressionFilter


def test_expression_requires_three_distinct_consistent_samples():
    f = ExpressionFilter()
    scores = [.01, .01, .01, .9, .03, .02, .02]
    assert f.update(1, scores, 1)['label'] == 'unknown'
    assert f.update(1, scores, 1.5)['label'] == 'unknown'
    assert f.update(1, scores, 2)['label'] == 'happy'
    assert f.update(2, scores, 2.5)['label'] == 'unknown'
    assert f.update(2, scores, 2.5)['stable_samples'] == 1
    assert f.update(2, scores, 5)['label'] == 'unknown'


def test_uncertain_expression_and_invalid_output_are_not_emotions():
    f = ExpressionFilter()
    for i in range(5):
        assert f.update(1, [.1, .1, .1, .3, .2, .1, .1], i)['label'] == 'unknown'
    for invalid in ([0]*6, [float('nan')]*7):
        with pytest.raises(ValueError):
            f.update(1, invalid, 10)


def test_moderate_expression_requires_consistency_and_preserves_uncertainty():
    from milo_next.behavior import Behavior
    f = ExpressionFilter()
    scores = [.06, .04, .05, .5, .2, .1, .05]
    for stamp in [1, 1.5, 2]:
        result = f.update(1, scores, stamp)
    assert result['label'] == 'happy'
    b = Behavior()
    b.observe([{'expression': result}])
    assert b.social['confidence'] == .5
    assert b.social['source'] == 'uncertain_visual_cue'


def test_transient_stale_camera_does_not_restart_audio_or_display():
    from milo_next.edge import Edge
    edge = Edge.__new__(Edge)
    edge.started_at, edge.perception_bad_since = 0, None
    assert not edge.perception_requires_restart({'state':'stale'}, 10)
    assert not edge.perception_requires_restart({'state':'stale'}, 31)
    assert not edge.perception_requires_restart({'state':'stale'}, 35)
    assert not edge.perception_requires_restart({'state':'running'}, 36)
    assert not edge.perception_requires_restart({'state':'stale'}, 37)
    assert edge.perception_requires_restart({'state':'stale'}, 45)

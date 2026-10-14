import numpy as np
import pytest

import config
from core.temporal import BASE_FEATURES, MotionMeter, TemporalFeatures, TemporalHead
from core.threat_engine import ThreatEngine
from ml.train_temporal import best_threshold, fit_logreg, sample_weights


def textured(seed=0, shape=(240, 320, 3)):
    return (np.random.default_rng(seed).random(shape) * 255).astype(np.uint8)


def test_motion_meter_detects_motion_and_lens_covering():
    m = MotionMeter()
    still = textured()
    for i in range(20):
        c = m.update(still, i * 0.1)
    assert c["motion"] == 0.0 and c["sharpness_drop"] == 0.0
    moved = m.update(textured(1), 2.0)
    assert moved["motion"] > 0.1
    covered = np.full_like(still, 12)                      # dark, featureless lens
    c = m.update(covered, 2.1)
    assert c["brightness"] < config.CAMERA_TAMPER_DARK and c["sharpness_drop"] > config.CAMERA_TAMPER_SHARPNESS_DROP


def test_camera_tamper_rule_requires_sustained_obstruction():
    e = ThreatEngine()
    bad = {"motion": 0, "motion_zone": 0, "brightness": 0.02, "sharpness": 0.1, "brightness_drop": 0.4, "sharpness_drop": 4.0}
    ok = {**bad, "brightness": 0.5, "sharpness_drop": 0.0}
    r = None
    for i in range(10):                                    # 1 s obstructed: not yet
        r = e.evaluate([], [], 100, 100, now=i * 0.1, cues=bad)
    assert "camera_tamper" not in {s["rule"] for s in r["signals"]}
    for i in range(10, 30):                                # > 2 s
        r = e.evaluate([], [], 100, 100, now=i * 0.1, cues=bad)
    assert "camera_tamper" in {s["rule"] for s in r["signals"]}
    r = e.evaluate([], [], 100, 100, now=3.1, cues=ok)
    assert "camera_tamper" not in {s["rule"] for s in r["signals"]}


def test_learned_anomaly_signal_uses_threshold():
    e = ThreatEngine()
    assert "learned_anomaly" not in {s["rule"] for s in e.evaluate([], [], 100, 100, now=0, anomaly=(0.4, 0.6))["signals"]}
    r = e.evaluate([], [], 100, 100, now=0.1, anomaly=(0.7, 0.6))
    assert r["raw_score"] == config.SCORE_LEARNED_ANOMALY


def test_temporal_features_window_and_new_tracks():
    tf = TemporalFeatures(window_seconds=1.0)
    p = lambda tid: {"class_name": "person", "track_id": tid, "confidence": 0.9, "bbox": (0, 0, 10, 30), "in_zone": True, "speed": 0.5}  # noqa: E731
    x1 = tf.update(0.0, [p(1)], [], {})
    x2 = tf.update(0.1, [p(1), p(2)], [], {})
    names = TemporalFeatures.names()
    assert len(x1) == len(names) == 2 * len(BASE_FEATURES)
    assert x2[names.index("max_new_tracks")] == 1.0 and x2[names.index("max_persons_zone")] == 2.0
    x3 = tf.update(5.0, [], [], {})                        # window slides past old frames
    assert x3[names.index("max_persons_zone")] == 0.0


def test_logreg_learns_separable_signal_and_head_roundtrip():
    rng = np.random.default_rng(0)
    n = 400
    y = (rng.random(n) < 0.3).astype(int)
    X = rng.normal(size=(n, 2 * len(BASE_FEATURES)))
    X[:, 0] += 3 * y
    vid = rng.integers(0, 8, n)
    mu, sd = X.mean(0), X.std(0)
    coef, b = fit_logreg((X - mu) / sd, y, sample_weights(y, vid), C=1.0)
    head = TemporalHead({"version": "t", "features": TemporalFeatures.names(), "mean": mu.tolist(),
                         "std": sd.tolist(), "coef": coef.tolist(), "intercept": b, "threshold": 0.5})
    probs = np.array([head.predict(x) for x in X])
    assert ((probs >= 0.5) == y).mean() > 0.9
    thr, j = best_threshold(y, probs)
    assert 0 < thr < 1 and j > 0.8
    with pytest.raises(ValueError):
        TemporalHead({**head.spec, "features": ["other"]})


def test_sample_weights_equalise_videos_and_classes():
    y = np.array([0, 0, 0, 0, 1, 1])
    vid = np.array([0, 0, 0, 0, 1, 1])
    w = sample_weights(y, vid)
    assert w[y == 0].sum() == pytest.approx(w[y == 1].sum())

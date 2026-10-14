import numpy as np
import pandas as pd
import pytest

from ml.datasets import atmav, openimages, scfd
from ml.evaluate_detector import IOU_THRESHOLDS, average_precision, box_iou, confusion, match_image
from ml.evaluate_pipeline import false_alarms_per_minute, roc_auc, threshold_metrics


# ── detector metrics ──────────────────────────────────────────────────────

def test_box_iou():
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], float)
    assert np.allclose(box_iou(a, b), [[1.0, 1 / 3, 0.0]])


def test_perfect_predictions_give_ap_1():
    gt = np.array([[0, 0, 10, 10], [20, 20, 40, 40]], float)
    tp = match_image(gt, np.array([0, 0]), np.array([0.9, 0.8]), gt, np.array([0, 0]))
    assert tp.all()
    assert np.allclose(average_precision(tp, np.array([0.9, 0.8]), 2), 1.0)


def test_ap_with_false_positive_ranked_first():
    # FP (conf .95) then TP (conf .9) for a single GT → precision envelope 0.5 at recall 1
    gt = np.array([[0, 0, 10, 10]], float)
    preds = np.array([[50, 50, 60, 60], [0, 0, 10, 10]], float)
    conf = np.array([0.95, 0.9])
    tp = match_image(preds, np.array([0, 0]), conf, gt, np.array([0]))
    ap = average_precision(tp, conf, 1)
    assert ap[0] == pytest.approx(0.5, abs=0.01)


def test_ap_missing_class_and_no_predictions():
    assert np.isnan(average_precision(np.zeros((0, 10), bool), np.zeros(0), 0)).all()
    assert (average_precision(np.zeros((0, 10), bool), np.zeros(0), 3) == 0).all()


def test_duplicate_detections_count_once():
    gt = np.array([[0, 0, 10, 10]], float)
    preds = np.array([[0, 0, 10, 10], [0, 0, 10, 10]], float)
    tp = match_image(preds, np.array([0, 0]), np.array([0.9, 0.8]), gt, np.array([0]))
    assert tp[:, 0].sum() == 1


def test_wrong_class_is_not_tp_but_appears_in_confusion():
    gt = {"boxes": np.array([[0, 0, 10, 10]], float), "cls": np.array([1])}
    pred = {"boxes": np.array([[0, 0, 10, 10]], float), "cls": np.array([2]), "conf": np.array([0.9])}
    tp = match_image(pred["boxes"], pred["cls"], pred["conf"], gt["boxes"], gt["cls"])
    assert not tp.any()
    m = confusion([gt], [pred], 3, 0.25)
    assert m[2, 1] == 1 and m[3, :].sum() == 0 and m[:, 3].sum() == 0
    assert len(IOU_THRESHOLDS) == 10


# ── pipeline metrics ──────────────────────────────────────────────────────

def test_roc_auc_known_values():
    assert roc_auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert roc_auc(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert roc_auc(np.array([0, 1]), np.array([0.5, 0.5])) == 0.5
    assert roc_auc(np.array([1, 1]), np.array([0.5, 0.5])) is None


def test_threshold_metrics_and_false_alarms():
    y = np.array([0, 0, 0, 1, 1, 0, 0, 0])
    s = np.array([0, 60, 0, 70, 70, 0, 60, 60])
    m = threshold_metrics(y, s, 50)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 3, 0, 3)
    rate, events, minutes = false_alarms_per_minute([(y, s, 1.0)], 50)
    assert events == 2                       # rising edges at frames 1 and 6 (normal); 3 is anomalous
    assert minutes == pytest.approx(6 / 60, abs=0.01)


# ── dataset tooling ───────────────────────────────────────────────────────

def test_clean_boxes_clips_dedups_and_drops_degenerate():
    rows = pd.DataFrame([
        {"XMin": -0.1, "XMax": 0.5, "YMin": 0.1, "YMax": 0.6, "cls": 0},     # clipped
        {"XMin": 0.0, "XMax": 0.5, "YMin": 0.1, "YMax": 0.6, "cls": 0},      # duplicate (Person+Man)
        {"XMin": 0.0, "XMax": 0.5, "YMin": 0.1, "YMax": 0.6, "cls": 1},      # same box, other class: kept
        {"XMin": 0.3, "XMax": 0.3005, "YMin": 0.1, "YMax": 0.6, "cls": 2},   # degenerate
    ])
    boxes = openimages.clean_boxes(rows)
    assert boxes == [(0, 0.0, 0.1, 0.5, 0.6), (1, 0.0, 0.1, 0.5, 0.6)]


def test_dhash_detects_near_duplicates():
    rng = np.random.default_rng(0)
    img = (rng.random((64, 64, 3)) * 255).astype(np.uint8)
    noisy = np.clip(img.astype(int) + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    other = (rng.random((64, 64, 3)) * 255).astype(np.uint8)
    h1, h2, h3 = (openimages.dhash(x) for x in (img, noisy, other))
    d = openimages._hamming_min(np.array([h2, h3], np.uint64), np.array([h1], np.uint64))
    assert d[0] <= openimages.NEAR_DUP_HAMMING < d[1]


def test_atmav_split_keeps_source_groups_together():
    from data.labels import ANNOTATIONS
    splits = atmav.make_splits(ANNOTATIONS)
    groups = {}
    for v, s in splits.items():
        groups.setdefault(atmav.source_group(v), set()).add(s)
    assert all(len(s) == 1 for s in groups.values())             # no video/subject leakage
    assert atmav.source_group("36b.mp4") == "36"
    assert {"train", "val", "test"} == set(splits.values())


def test_atmav_annotation_validation():
    probes = {"a.mp4": {"readable": True, "frames_reported": 100, "frames_decoded": 98}}
    errors, notes = atmav.validate_annotations({"a.mp4": (100, 25, [(10, 50), (40, 99)])}, probes)
    assert any("overlapping" in e for e in errors) and any("decodable" in e for e in errors)
    assert notes and "98" in notes[0]
    errors, _ = atmav.validate_annotations({"a.mp4": (100, 25, [(10, 50)])}, probes)
    assert errors == []


def test_scfd_group_parsing(tmp_path):
    p = tmp_path / "videos.txt"
    p.write_text("https://www.youtube.com/watch?v=abcdefgh123\nfi001: 0.1 - 0.2\nnofi002: 0.3 - 0.4\n"
                 "https://youtu.be/zzzzzzzz\nfi003: 1 - 2\n\nnofi010 - nofi012 : CamNet Dataset\n")
    g = scfd.parse_groups(str(p))
    assert g["fi001"] == g["nofi002"] == "abcdefgh123" and g["fi003"] == "zzzzzzzz"
    assert g["nofi011"] == "ds-camnet-dataset"

# Threat-pipeline evaluation (video-level labels)

Generated 2026-10-14T10:14:00+00:00 by `ml/evaluate_pipeline.py`. Incident threshold = score ≥ 50, HIGH = ≥ 75. default ATM zone (5%-95% of frame) for every video — no per-video tuning. All values are computed by the script.

## ATMA-V — `test` split (frame-level temporal labels)

| Variant | Frame AUC | P @50 | R @50 | F1 @50 | FPR @50 | P @75 | R @75 | FPR @75 | Video detection rate | Videos w/ false alarm | False alarms / min normal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `v0` | 0.4988 | 0.6826 | 0.2841 | 0.4012 | 0.2303 | 0.5564 | 0.0279 | 0.0388 | 0.6923 | 0.2857 | 3.616 |
| `v2-coco` | 0.4074 | 0.5557 | 0.1293 | 0.2098 | 0.1803 | 1.0 | 0.0043 | 0.0 | 0.4615 | 0.3333 | 2.8299 |
| `v2-coco+temporal` | 0.6276 | 0.7019 | 0.9119 | 0.7932 | 0.6753 | 0.7511 | 0.3533 | 0.2042 | 1.0 | 0.619 | 2.9871 |
| `v2-sentinelx` | 0.4394 | 0.5653 | 0.1231 | 0.2022 | 0.1651 | 0.532 | 0.0345 | 0.0529 | 0.6154 | 0.2381 | 1.1005 |
| `v2-sentinelx+temporal` | 0.6423 | 0.7309 | 0.9438 | 0.8238 | 0.6058 | 0.701 | 0.342 | 0.2544 | 1.0 | 0.5238 | 1.8866 |

21 videos (13 with anomalies), 26163 frames (16627 anomalous).

## SCFD — `test` split (clip-level fight / noFight)

| Variant | Clip AUC (peak score) | Clip AUC (mean score) | P @50 | R @50 | FPR @50 |
|---|---|---|---|---|---|
| `v0` | 0.6386 | 0.62 | 0.75 | 0.0674 | 0.0222 |
| `v2-coco` | 0.6359 | 0.6331 | 1.0 | 0.0112 | 0.0 |
| `v2-coco+temporal` | 0.7169 | 0.7219 | 0.594 | 0.8876 | 0.6 |
| `v2-sentinelx` | 0.6031 | 0.5833 | 0.8571 | 0.0674 | 0.0111 |
| `v2-sentinelx+temporal` | 0.6512 | 0.7112 | 0.5473 | 0.9101 | 0.7444 |

## Rule activations (ATMA-V, number of evaluated frames where the rule fired)

- `v2-coco` (test): close_proximity=739, loitering=809, multiple_persons=1512, rapid_motion=95, vandalism=697, weapon=469
- `v2-coco+temporal` (test): close_proximity=739, learned_anomaly=5419, loitering=809, multiple_persons=1512, rapid_motion=95, vandalism=697, weapon=469
- `v2-sentinelx` (test): blunt_tool=85, close_proximity=301, face_concealment=104, firearm=302, loitering=300, multiple_persons=503, rapid_motion=11, threat_object=821, vandalism=126, weapon=464
- `v2-sentinelx+temporal` (test): blunt_tool=85, close_proximity=301, face_concealment=104, firearm=302, learned_anomaly=5935, loitering=300, multiple_persons=503, rapid_motion=11, threat_object=821, vandalism=126, weapon=464

v0 has no structured rule output (its reasons are free text), so no activation counts are shown for it.

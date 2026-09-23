# data/annotations.py
# Clean API over the raw label data.
# Used by the dashboard to show ground truth validation.

from data.labels import ANNOTATIONS


def get_video_info(video_name: str) -> dict:
    """Return metadata for a video file."""
    if video_name not in ANNOTATIONS:
        return {"found": False}
    total, fps, segments = ANNOTATIONS[video_name]
    return {
        "found":    True,
        "total_frames": total,
        "fps":      fps,
        "segments": segments,
        "has_anomaly": len(segments) > 0,
    }


def is_anomalous_frame(video_name: str, frame_number: int) -> bool:
    """
    Returns True if the given frame falls inside any labeled anomaly segment.
    Used for ground truth overlay on the dashboard.
    """
    if video_name not in ANNOTATIONS:
        return False
    _, _, segments = ANNOTATIONS[video_name]
    for start, end in segments:
        if start <= frame_number <= end:
            return True
    return False


def get_anomaly_segments(video_name: str) -> list:
    """Return list of (start, end) anomaly frame ranges for a video."""
    if video_name not in ANNOTATIONS:
        return []
    return ANNOTATIONS[video_name][2]
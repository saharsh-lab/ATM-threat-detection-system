# ml/classes.py
# Loads the SentinelX detector class schema from data/registry/classes.yaml.

import os
from functools import lru_cache

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSES_YAML = os.path.join(REPO_ROOT, "data", "registry", "classes.yaml")


@lru_cache(maxsize=1)
def load_schema(path: str = CLASSES_YAML) -> dict:
    with open(path) as f:
        schema = yaml.safe_load(f)
    ids = [c["id"] for c in schema["classes"]]
    if ids != list(range(len(ids))):
        raise ValueError(f"class ids must be contiguous from 0, got {ids}")
    return schema


def class_names() -> list[str]:
    return [c["name"] for c in load_schema()["classes"]]


def openimages_mid_to_class() -> dict[str, int]:
    """Open Images LabelName MID -> SentinelX class id."""
    out = {}
    for c in load_schema()["classes"]:
        for mid in c["openimages"]:
            out[mid] = c["id"]
    return out


def coco_to_class() -> dict[int, int]:
    """COCO-80 class id -> SentinelX class id (for mapping the baseline model)."""
    out = {}
    for c in load_schema()["classes"]:
        for coco_id in c["coco"]:
            out[coco_id] = c["id"]
    return out

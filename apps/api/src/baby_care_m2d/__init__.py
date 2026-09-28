"""Pinned V1 B inference runtime; no training, downloading, or API routes."""

from .registry import LABEL_MAPPING_VERSION, MODEL_VERSION, PREPROCESS_VERSION

__all__ = ["LABEL_MAPPING_VERSION", "MODEL_VERSION", "PREPROCESS_VERSION"]

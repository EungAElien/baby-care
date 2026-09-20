"""Synthetic LLM prevalidation tools for B-02.

This package is intentionally separate from the production API routes.  It does not
grant database access, persist conversations, or enable external processing.
"""

from baby_care_api.models.normalization import MODEL_ID

__all__ = ["MODEL_ID"]

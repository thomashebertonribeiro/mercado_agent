"""Collection mode definitions for the Intelligent Collector.

This module defines the :class:`CollectionMode` enum, which represents the
four supported data-collection strategies. Import this enum wherever a
collection mode needs to be specified or compared.
"""

from enum import Enum


class CollectionMode(str, Enum):
    """Supported collection modes for :class:`IntelligentCollector`.

    Inherits from ``str`` so instances compare equal to their string values
    and serialise cleanly in JSON and database columns.
    """

    BY_PRODUCT  = "by_product"
    BY_CATEGORY = "by_category"
    INCREMENTAL = "incremental"
    FULL        = "full"

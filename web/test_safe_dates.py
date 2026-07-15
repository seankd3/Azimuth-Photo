from datetime import datetime
from unittest.mock import patch

import pytest

from core import dates
from features.collections.suggestions import _event_title


@pytest.mark.parametrize(
    "value",
    ["0000-01-01 00:00:00", "1904-01-01", -2082844800.0, "9999-12-31 23:59:59", "not a date"],
)
def test_untrusted_date_values_never_escape_date_helpers(value):
    timestamp = dates.parse_taken_timestamp(value)
    assert timestamp is None or isinstance(timestamp, float)

    display_value = timestamp if timestamp is not None else value
    displayed = dates.safe_datetime_fromtimestamp(display_value)
    assert displayed is None or isinstance(displayed, datetime)
    if displayed is not None:
        safe_timestamp = dates.safe_timestamp(displayed)
        assert safe_timestamp is None or isinstance(safe_timestamp, float)


def test_windows_range_errors_return_none():
    class BrokenTimestamp:
        def timestamp(self):
            raise OSError(22, "Invalid argument")

    assert dates.safe_timestamp(BrokenTimestamp()) is None
    with patch("core.dates.datetime") as mocked_datetime:
        mocked_datetime.fromtimestamp.side_effect = OSError(22, "Invalid argument")
        assert dates.safe_datetime_fromtimestamp(-2082844800.0) is None


def test_suggestion_titles_skip_unreadable_stored_timestamps():
    assert _event_title(-2082844800.0, -2082841200.0)
    assert _event_title(float("nan"), float("nan")) == "Undated"

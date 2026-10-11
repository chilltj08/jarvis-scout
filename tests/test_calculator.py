from decimal import Decimal

import pytest

from jarvis_scout.calculator import calculate


def test_expected_profit_roi_and_margin():
    result = calculate("12", "30", "0.15", "3", "1", "0.50")
    assert result.referral_fee == Decimal("4.50")
    assert result.estimated_profit == Decimal("9.00")
    assert result.roi_percent == Decimal("69.23")
    assert result.margin_percent == Decimal("30.00")


@pytest.mark.parametrize(
    "args",
    [
        ("-1", "20", "0.15", "2"),
        ("1", "20", "1.1", "2"),
        ("1", "0", "0.15", "2"),
        ("0", "20", "0.15", "2"),
    ],
)
def test_invalid_inputs_raise(args):
    with pytest.raises(ValueError):
        calculate(*args)

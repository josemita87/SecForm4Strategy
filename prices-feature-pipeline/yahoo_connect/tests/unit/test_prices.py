"""Prices refresh incrementally, but never mix adjustment bases and never store a provisional close.

Under test:
    prices-feature-pipeline/yahoo_connect/src/prices.py
"""

import pandas as pd
from secform4strategy_clients.fakes.market_data import FakeMarketData
from src.constants import REBASE_LOOKBACK_DAYS
from src.prices import new_prices, resume_points

from tests.factories import TODAY, bars, stored

#: 60 stored trading days ending before last week, then the days since (not yet stored)
STORED_DAYS = pd.bdate_range(end=TODAY - pd.Timedelta(days=10), periods=60)
NEW_DAYS = pd.bdate_range(STORED_DAYS[-1] + pd.Timedelta(days=1), TODAY - pd.Timedelta(days=1))
ALL_DAYS = STORED_DAYS.append(NEW_DAYS)


def refresh(
    store: pd.DataFrame, yahoo: dict[str, pd.DataFrame], tickers: list[str] | None = None
) -> tuple[pd.DataFrame, FakeMarketData]:
    """Refresh ``tickers`` (default: every ticker Yahoo has) against the ``store``d bars, in one batch."""
    market, tickers = FakeMarketData(yahoo), tickers or list(yahoo)
    batches = list(new_prices(market, tickers, resume_points(store), batch_size=len(tickers)))
    return pd.concat(batches, ignore_index=True), market


def full_refetches(market: FakeMarketData) -> list[str]:
    """The tickers downloaded again in full (calls without a start)."""
    return sorted(symbol for symbols, starts in market.calls for symbol in symbols if symbol not in starts)


class TestResumePoints:
    """A refresh starts ``REBASE_LOOKBACK_DAYS`` stored days back and remembers their factors."""

    def test_resume_starts_at_the_lookback_window_with_its_factors(self) -> None:
        point = resume_points(stored({'A': bars(STORED_DAYS, factor=0.98)}))['A']
        assert point.start == STORED_DAYS[-REBASE_LOOKBACK_DAYS].date()
        assert len(point.factors) == REBASE_LOOKBACK_DAYS
        assert set(point.factors.values()) == {0.98}

    def test_a_ticker_without_stored_bars_has_no_resume_point(self) -> None:
        assert 'NEW' not in resume_points(stored({'A': bars(STORED_DAYS)}))


class TestIncrementalRefresh:
    """An unchanged basis appends the new days only."""

    def test_unchanged_prices_fetch_the_lookback_and_new_days_once(self) -> None:
        out, market = refresh(stored({'A': bars(STORED_DAYS, 0.98)}), {'A': bars(ALL_DAYS, 0.98)})
        assert full_refetches(market) == []
        assert out['date'].min() == STORED_DAYS[-REBASE_LOOKBACK_DAYS]

    def test_a_new_ticker_fetches_its_full_history(self) -> None:
        out, market = refresh(stored({'A': bars(STORED_DAYS)}), {'A': bars(ALL_DAYS), 'NEW': bars(ALL_DAYS)})
        assert full_refetches(market) == ['NEW']
        assert len(out[out['ticker'] == 'NEW']) == len(ALL_DAYS)


class TestRebasing:
    """A split or dividend applied to stored days brings the ticker's full history again."""

    def test_an_action_after_the_stored_days_refetches_in_full(self) -> None:
        after = NEW_DAYS[0].strftime('%Y-%m-%d')
        yahoo = {'A': bars(ALL_DAYS, 0.98, actions={after: (2.0, 0.0)})}
        _, market = refresh(stored({'A': bars(STORED_DAYS, 0.98)}), yahoo)
        assert full_refetches(market) == ['A']

    def test_an_action_recorded_late_inside_the_stored_days_refetches_in_full(self) -> None:
        # Yahoo now applies a dividend dated inside the stored days, so their factors moved
        _, market = refresh(stored({'A': bars(STORED_DAYS, 0.98)}), {'A': bars(ALL_DAYS, 0.97)})
        assert full_refetches(market) == ['A']

    def test_an_action_on_the_last_stored_day_is_already_on_the_stored_basis(self) -> None:
        last = STORED_DAYS[-1].strftime('%Y-%m-%d')
        action = {last: (0.0, 0.5)}
        _, market = refresh(stored({'A': bars(STORED_DAYS, 0.98, action)}), {'A': bars(ALL_DAYS, 0.98, action)})
        assert full_refetches(market) == []

    def test_stored_days_that_no_longer_come_back_refetch_in_full(self) -> None:
        _, market = refresh(stored({'A': bars(STORED_DAYS)}), {'A': bars(NEW_DAYS)})
        assert full_refetches(market) == ['A']


class TestFinalBarsOnly:
    """A bar is stored only once its close has printed."""

    def test_a_bar_whose_close_has_not_printed_is_left_out(self) -> None:
        tomorrow = TODAY + pd.Timedelta(days=1)
        out, _ = refresh(stored({'A': bars(STORED_DAYS)}), {'A': bars(ALL_DAYS.append(pd.DatetimeIndex([tomorrow])))})
        assert out['date'].max() == ALL_DAYS[-1]

    def test_a_batch_where_every_download_fails_yields_typed_empty_bars(self) -> None:
        out, _ = refresh(stored({'A': bars(STORED_DAYS)}), {}, tickers=['A'])
        assert out.empty
        assert out['date'].dtype.kind == 'M'

"""Prices refresh incrementally, but never mix adjustment bases and never store a provisional close.

The live path returns the last days' final closes and stores nothing.

Under test:
    prices-feature-pipeline/yahoo_connect/src/prices.py
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from secform4strategy_clients.fakes.feature_store import FakeFeatureStore
from secform4strategy_clients.fakes.market_data import FakeMarketData
from src.constants import LIVE_LOOKBACK_DAYS, REBASE_LOOKBACK_DAYS, FeatureGroup
from src.prices import history_start, keyed, new_prices, recent_closes, refresh_prices, resume_points

from tests.factories import TODAY, bars, stored

if TYPE_CHECKING:
    from src.prices import PriceBatch

#: 60 stored trading days ending before last week, then the days since (not yet stored)
STORED_DAYS = pd.bdate_range(end=TODAY - pd.Timedelta(days=10), periods=60)
NEW_DAYS = pd.bdate_range(STORED_DAYS[-1] + pd.Timedelta(days=1), TODAY - pd.Timedelta(days=1))
ALL_DAYS = STORED_DAYS.append(NEW_DAYS)
#: The first day a full refetch downloads, inside the stored days
START = STORED_DAYS[10].date()


def fetch(
    store: pd.DataFrame, yahoo: dict[str, pd.DataFrame], tickers: list[str] | None = None
) -> tuple[PriceBatch, FakeMarketData]:
    """Fetch ``tickers`` (default: every ticker Yahoo has) against the ``store``d bars, in one batch."""
    market, tickers = FakeMarketData(yahoo), tickers or list(yahoo)
    (batch,) = new_prices(market, tickers, resume_points(store), batch_size=len(tickers), start=START)
    return batch, market


def refresh(
    store: pd.DataFrame, yahoo: dict[str, pd.DataFrame], tickers: list[str] | None = None
) -> tuple[pd.DataFrame, FakeMarketData]:
    """Fetch as ``fetch`` does, with the bars to upsert and the full histories in one frame."""
    batch, market = fetch(store, yahoo, tickers)
    return pd.concat(batch, ignore_index=True), market


def full_refetches(market: FakeMarketData) -> list[str]:
    """The tickers downloaded in full, meaning from the history start or from their first stored day."""
    return sorted(
        symbol
        for symbols, starts in market.calls
        for symbol in symbols
        if starts.get(symbol) in {START, STORED_DAYS[0].date()}
    )


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

    def test_a_new_ticker_fetches_from_the_history_start(self) -> None:
        out, market = refresh(stored({'A': bars(STORED_DAYS)}), {'A': bars(ALL_DAYS), 'NEW': bars(ALL_DAYS)})
        assert full_refetches(market) == ['NEW']
        assert out[out['ticker'] == 'NEW']['date'].min().date() == START
        assert len(out[out['ticker'] == 'NEW']) == len(ALL_DAYS[ALL_DAYS.date >= START])


class TestHistoryStart:
    """A full refetch reaches back a fixed number of years."""

    def test_history_start_is_the_given_years_before_today(self) -> None:
        assert history_start(15) == (pd.Timestamp.today() - pd.DateOffset(years=15)).date()


class TestRebasing:
    """A split or dividend applied to stored days brings the ticker's full history again."""

    def test_an_action_after_the_stored_days_refetches_in_full(self) -> None:
        after = NEW_DAYS[0].strftime('%Y-%m-%d')
        yahoo = {'A': bars(ALL_DAYS, 0.98, actions={after: (2.0, 0.0)})}
        out, market = refresh(stored({'A': bars(STORED_DAYS, 0.98)}), yahoo)
        assert full_refetches(market) == ['A']
        # The stored days start before the history start, so the reload covers all of them
        assert out['date'].min() == STORED_DAYS[0]

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


class TestReload:
    """A rebased ticker's full history is kept apart, to replace its stored bars; the others are upserted."""

    def test_a_rebased_ticker_is_reloaded_and_the_others_appended(self) -> None:
        store = stored({'A': bars(STORED_DAYS, 0.98), 'B': bars(STORED_DAYS, 0.98)})
        batch, _ = fetch(store, {'A': bars(ALL_DAYS, 0.97), 'B': bars(ALL_DAYS, 0.98)})
        assert set(batch.reloaded['ticker']) == {'A'}
        assert set(batch.appended['ticker']) == {'B'}

    def test_a_reload_starts_at_the_history_start_when_the_stored_days_start_later(self) -> None:
        late = STORED_DAYS[20:]
        batch, _ = fetch(stored({'A': bars(late, 0.98)}), {'A': bars(ALL_DAYS, 0.97)})
        assert batch.reloaded['date'].min().date() == START

    def test_a_failed_reload_stores_nothing_for_the_ticker(self) -> None:
        resume = resume_points(stored({'A': bars(STORED_DAYS, 0.98)}))
        market = _FailingReload(FakeMarketData({'A': bars(ALL_DAYS, 0.97)}))
        (batch,) = new_prices(market, ['A'], resume, batch_size=1, start=START)
        assert batch.appended.empty
        assert batch.reloaded.empty


@dataclass
class _FailingReload:
    """Market data whose first download succeeds and every later one returns no bars."""

    market: FakeMarketData

    def price_histories(self, symbols: Iterable[str], starts: Mapping[str, date] | None = None) -> pd.DataFrame:
        """Return the market's bars on the first call and none afterwards."""
        fetched = self.market.price_histories(symbols, starts)
        return fetched if len(self.market.calls) == 1 else fetched.iloc[:0]


class TestUnpricedDays:
    """A bar without a close is never stored, and a stored one is cleared by a reload."""

    def test_a_fetched_bar_without_a_close_is_left_out_and_rebases_nothing(self) -> None:
        yahoo = bars(ALL_DAYS, 0.98)
        yahoo.loc[len(yahoo) - 1, ['close', 'close_as_traded']] = np.nan
        out, market = refresh(stored({'A': bars(STORED_DAYS, 0.98)}), {'A': yahoo})
        assert full_refetches(market) == []
        assert out['close'].notna().all()

    def test_a_stored_day_without_a_close_reloads_the_ticker(self) -> None:
        history = bars(STORED_DAYS, 0.98)
        history.loc[len(history) - 1, ['close', 'close_as_traded']] = np.nan
        _, market = refresh(stored({'A': history}), {'A': bars(ALL_DAYS, 0.98)})
        assert full_refetches(market) == ['A']


class TestRefreshPrices:
    """A rebased ticker's stored bars are replaced in full, so the prices group never mixes two bases."""

    def test_a_rebased_ticker_is_on_the_new_basis_on_every_stored_day(self) -> None:
        store = FakeFeatureStore(
            {FeatureGroup.PRICES: keyed(stored({'A': bars(STORED_DAYS, 0.98), 'B': bars(STORED_DAYS, 0.98)}))}
        )
        market = FakeMarketData({'A': bars(ALL_DAYS, 0.97), 'B': bars(ALL_DAYS, 0.98)})
        refresh_prices(store, market, ['A', 'B'], buffer_size=2, history_years=15)
        prices = store.tables[FeatureGroup.PRICES]
        a = prices[prices['ticker'] == 'A']
        assert set((a['close'] / a['close_as_traded']).round(6)) == {0.97}
        assert len(a) == len(ALL_DAYS) == a['date'].nunique()
        assert len(prices[prices['ticker'] == 'B']) == len(ALL_DAYS)
        assert store.replaces == [(FeatureGroup.PRICES, {'ticker': ['A']})]

    def test_unchanged_prices_are_upserted_without_a_replace(self) -> None:
        store = FakeFeatureStore({FeatureGroup.PRICES: keyed(stored({'A': bars(STORED_DAYS, 0.98)}))})
        refresh_prices(store, FakeMarketData({'A': bars(ALL_DAYS, 0.98)}), ['A'], buffer_size=1, history_years=15)
        assert len(store.tables[FeatureGroup.PRICES]) == len(ALL_DAYS)
        assert store.replaces == []


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


class TestRecentCloses:
    """The live path gets each ticker's final as-traded closes of the last days, as JSON records."""

    def test_only_final_bars_come_back(self) -> None:
        unpriced = bars(ALL_DAYS)
        unpriced.loc[len(unpriced) - 1, ['close', 'close_as_traded']] = np.nan
        tomorrow = bars(pd.DatetimeIndex([TODAY + pd.Timedelta(days=1)]))
        closes = recent_closes(FakeMarketData({'A': pd.concat([unpriced, tomorrow], ignore_index=True)}), ['A'])
        start = pd.Timestamp(TODAY.date() - timedelta(days=LIVE_LOOKBACK_DAYS))
        assert [close['date'] for close in closes] == [f'{day:%Y-%m-%d}' for day in ALL_DAYS[:-1] if day >= start]

    def test_the_window_reaches_a_close_across_a_long_weekend(self) -> None:
        # The last close is five or more calendar days back, as after a weekend and two holidays
        days = pd.bdate_range(end=TODAY - pd.Timedelta(days=5), periods=30)
        market, last = FakeMarketData({'A': bars(days)}), days[-1]
        closes = recent_closes(market, ['A'])
        assert closes[-1] == {'ticker': 'A', 'date': last.strftime('%Y-%m-%d'), 'close_as_traded': 100.0}
        assert market.calls == [(['A'], {'A': TODAY.date() - timedelta(days=LIVE_LOOKBACK_DAYS)})]

    def test_a_failed_download_leaves_its_ticker_out(self) -> None:
        closes = recent_closes(FakeMarketData({'A': bars(ALL_DAYS)}), ['A', 'GONE'])
        assert {close['ticker'] for close in closes} == {'A'}
        assert recent_closes(FakeMarketData({}), ['GONE']) == []

    def test_the_records_are_json(self) -> None:
        closes = recent_closes(FakeMarketData({'A': bars(ALL_DAYS)}), ['A'])
        assert json.loads(json.dumps(closes)) == closes

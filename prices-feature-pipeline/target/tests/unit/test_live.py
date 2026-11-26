"""The live path values BIR4 rows at the closes yahoo-connect sends, exactly as the batch path values them from P.

Under test:
    prices-feature-pipeline/target/src/constants.py: LiveScoreEvent
    prices-feature-pipeline/target/src/helpers.py: daily_closes
    prices-feature-pipeline/target/src/main.py: publish_inference_rows
"""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from secform4strategy_clients.fakes.feature_store import FakeFeatureStore
from src.constants import PRICE_COLUMNS, FeatureGroup, LiveScoreEvent, ReferenceData
from src.helpers import daily_closes, normalize_prices
from src.main import publish_inference_rows

from tests.factories import ny, transactions

#: Two weeks of bars, stored as P stores them (float32 prices, categorical ticker)
DAYS = pd.bdate_range('2024-03-01', '2024-03-14')
P = pd.DataFrame(
    {
        'ticker': pd.Categorical(['AAA'] * len(DAYS)),
        'date': DAYS,
        'open': np.float32(50.0),
        'close': np.linspace(50.0, 60.0, len(DAYS), dtype='float32'),
        'close_as_traded': np.linspace(100.1, 120.3, len(DAYS), dtype='float32'),
    }
)
REFERENCE = ReferenceData(
    market_caps=pd.DataFrame({'ticker': ['AAA'], 'snapshot': pd.to_datetime(['2024-03-01']), 'market_cap': [1e9]}),
    companies=pd.DataFrame(
        {'exchange': ['NYSE'], 'sic': ['2834'], 'sic_group': ['Biotechnology']}, index=['0000000001']
    ),
)
#: One label of AAA known before both rows
LABELS = pd.DataFrame(
    {
        'ticker': ['AAA'],
        'accepted_at': [ny('2024-01-02 18:00')],
        'label_known_at': [ny('2024-02-01 16:00')],
        'pct_change': [-0.1],
        'avg_target_expanding': [-0.1],
    }
)
#: A sale and a buy on different days, both after the first bar
TRANSACTIONS = transactions(
    {'accepted_at': '2024-03-12 18:00', 'date': '2024-03-12', 'price': 115.0},
    {'accepted_at': '2024-03-14 10:00', 'date': '2024-03-13', 'coding': 'P', 'insider_cik': 'I2', 'accession': 'A-2'},
)


def event(bars: pd.DataFrame) -> dict[str, object]:
    """The JSON yahoo-connect returns for ``bars``."""
    return {
        'tickers': ['AAA'],
        'prices': bars[['ticker', 'date', 'close_as_traded']]
        .assign(ticker=bars['ticker'].astype(str), date=bars['date'].dt.strftime('%Y-%m-%d'))
        .to_dict('records'),
    }


def published(prices: pd.DataFrame) -> pd.DataFrame:
    """The BIR4 rows ``publish_inference_rows`` upserts for ``TRANSACTIONS`` valued at ``prices``."""
    rows = publish_inference_rows(
        store := FakeFeatureStore({FeatureGroup.RT4: LABELS}), TRANSACTIONS, prices, REFERENCE, pd.Timedelta(days=30)
    )
    assert len(store.tables.get(FeatureGroup.BIR4, ())) == len(rows)
    return rows.sort_values('key', ignore_index=True)


class TestSameFeaturesAsFromP:
    """The event's closes give the BIR4 rows that the same bars read from P give."""

    def test_event_prices_build_the_rows_p_builds(self) -> None:
        from_p = published(normalize_prices(FakeFeatureStore({'p': P}).read('p', columns=PRICE_COLUMNS)))
        from_event = published(daily_closes(LiveScoreEvent.model_validate(event(P)).prices))
        assert len(from_p) == 2
        assert from_p['tx_value'].notna().all()
        pd.testing.assert_frame_equal(from_event, from_p)

    def test_a_row_accepted_on_or_before_the_first_close_is_left_as_stored(self) -> None:
        recent = P[P['date'] >= '2024-03-12']
        rows = published(daily_closes(LiveScoreEvent.model_validate(event(recent)).prices))
        assert rows['accepted_at'].dt.tz_convert('America/New_York').dt.day.tolist() == [14]

    def test_a_ticker_without_closes_is_left_as_stored(self) -> None:
        both = pd.concat([TRANSACTIONS, TRANSACTIONS.assign(ticker='BBB')], ignore_index=True)
        rows = publish_inference_rows(
            FakeFeatureStore({FeatureGroup.RT4: LABELS}),
            both,
            daily_closes(LiveScoreEvent.model_validate(event(P)).prices),
            REFERENCE,
            pd.Timedelta(days=30),
        )
        assert set(rows['ticker'].astype(str)) == {'AAA'}
        assert rows['tx_value'].notna().all()

    def test_an_event_without_closes_publishes_no_rows(self) -> None:
        assert published(daily_closes(LiveScoreEvent.model_validate({'tickers': ['AAA'], 'prices': []}).prices)).empty


class TestEventValidation:
    """A malformed live-score input fails the invocation before any read."""

    @pytest.mark.parametrize(
        'bad',
        [
            {'tickers': ['AAA']},
            {'tickers': ['AAA'], 'prices': [{'ticker': 'AAA', 'date': 'yesterday', 'close_as_traded': 1.0}]},
            {'tickers': ['AAA'], 'prices': [{'ticker': 'AAA', 'date': '2024-03-01', 'close_as_traded': 'n/a'}]},
            {'tickers': ['AAA'], 'prices': [{'ticker': 'AAA', 'date': '2024-03-01'}]},
        ],
    )
    def test_a_malformed_event_is_rejected(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            LiveScoreEvent.model_validate(bad)

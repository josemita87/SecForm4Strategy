"""Entry point of the live-performance report: wires the read-only ports from config and prints the report.

Run with ``make live-performance`` (docs/aws.md#measuring-live-performance). It reads the live predictions, the
paper account's latest 500 orders and its open positions, the RT4 labels of the predicted rows, and each scoring
model's label return. It writes nothing.
"""

import sys
from typing import TYPE_CHECKING

import pandas as pd
from config import LocalModels, S3Models, config
from constants import Col, FeatureGroup, Source
from paths import SPEC_PATH
from performance import measure
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from secform4strategy_clients.broker import AlpacaClient
from secform4strategy_clients.feature_groups import load_feature_group_catalog
from secform4strategy_clients.feature_store import IcebergFeatureStore
from secform4strategy_clients.model_store import LocalModelStore
from secform4strategy_clients.s3_model_store import S3ModelStore

if TYPE_CHECKING:
    from performance import LivePerformance
    from secform4strategy_clients.ports import ModelFiles


def rate(value: float | None, of: int) -> str:
    """Return a share as a percentage with its sample size, or n/a while nothing is counted."""
    return 'n/a (no labelled rows)' if value is None else f'{value:.1%} of {of}'


def report(p: LivePerformance) -> str:
    """Return the report as text."""
    lines = [
        f'Live predictions: {p.scored} rows scored, {p.signals} at or above the probability cutoff',
        f'Paper shorts: {p.orders} placed, {p.filled} filled; {p.unrecorded_orders} other sell orders in the account',
        f'P&L marked at the current price: {p.total_pnl:+.2f} USD over {sum(t.pnl is not None for t in p.trades)} '
        'filled and open shorts',
    ]
    if p.trades:
        lines.append(pd.DataFrame(p.trades).to_string(index=False, float_format=lambda x: f'{x:.4f}'))
    lines += [
        f'Labelled rows (RT4): {p.labelled} of {p.scored}; labelled signals: {p.labelled_signals}',
        f'Hit rate of signals (short winners): {rate(p.hit_rate, p.labelled_signals)}',
        f'Base rate of scored rows: {rate(p.base_rate, p.labelled)}',
    ]
    if p.calibration:
        lines.append('Calibration of labelled rows:')
        lines.append(pd.DataFrame(p.calibration).to_string(index=False, float_format=lambda x: f'{x:.3f}'))
    return '\n'.join(lines)


if __name__ == '__main__':
    store = IcebergFeatureStore(
        load_catalog('default'),
        config.feature_store_namespace,
        load_feature_group_catalog(SPEC_PATH, config.feature_group_version),
    )
    try:
        predictions = store.read(FeatureGroup.PREDICTIONS, where={Col.SOURCE: Source.LIVE})
    except NoSuchTableError:
        sys.exit('No predictions yet: the live path creates the table with its first scored row')
    if predictions.empty:
        sys.exit('No live predictions yet')
    models: ModelFiles
    match config.model_store:
        case LocalModels() as local:
            models = LocalModelStore(local.path)
        case S3Models() as s3:
            models = S3ModelStore(s3.region, s3.bucket, s3.prefix)
    broker = AlpacaClient(config.alpaca_api_key, config.alpaca_api_secret, config.alpaca_base_url)
    labels = store.read(
        FeatureGroup.RT4,
        where={Col.KEY: predictions[Col.ROW_KEY].unique().tolist()},
        columns=(Col.KEY, Col.PCT_CHANGE),
    ).dropna()
    print(
        report(
            measure(
                predictions,
                broker.orders(500),
                broker.positions(),
                dict(zip(labels[Col.KEY], labels[Col.PCT_CHANGE], strict=True)),
                {version: models.metadata(version).label_return for version in predictions[Col.MODEL_VERSION].unique()},
            )
        )
    )

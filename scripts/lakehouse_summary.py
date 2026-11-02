"""Print every feature-store table's size and time span, and the latest model rows: a check between pipeline stages.

Runs inside the target image (``make lakehouse-summary``), where the catalog paths in ``.lakehouse/`` resolve.
"""

import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchNamespaceError


def main() -> None:
    """Print one line per table, then the five latest rows of RT4 and BIR4."""
    catalog = load_catalog('default')
    try:
        identifiers = sorted(catalog.list_tables('secform4strategy'))
    except NoSuchNamespaceError:
        print('The lakehouse is empty: run the sec stack first.')
        return
    pd.set_option('display.width', 220)
    pd.set_option('display.max_columns', 14)
    for identifier in identifiers:
        frame = catalog.load_table(identifier).scan().to_pandas()
        time = next((col for col in ('accepted_at', 'date') if col in frame), None)
        span = f', {frame[time].min()} .. {frame[time].max()}' if time and len(frame) else ''
        tickers = f', {frame["ticker"].nunique()} tickers' if 'ticker' in frame else ''
        print(f'{".".join(identifier)}: {len(frame)} rows{tickers}{span}')
        if identifier[-1].startswith(('rt4', 'bir4')) and len(frame):
            columns = [
                c for c in ('key', 'accepted_at', 'n_insiders', 'tx_value', 'total_held', 'pct_change') if c in frame
            ]
            print(frame.sort_values('accepted_at')[columns].tail(5).to_string(index=False), end='\n\n')


if __name__ == '__main__':
    main()

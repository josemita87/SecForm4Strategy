# /// script
# requires-python = ">=3.14"
# dependencies = ["pyiceberg[glue,pyarrow]>=0.12,<0.13"]
# ///
"""Print, or with ``--apply`` set, the Iceberg table properties maintenance relies on (docs/aws.md#iceberg-maintenance).

Run with ``AWS_PROFILE=secform4 uv run scripts/iceberg_table_properties.py [--apply]``; without ``--apply`` it reads.
Each property update is one metadata commit; a writer that races it retries.
"""

import sys

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError

#: Writers retry a lost commit themselves (``with_backoff``), reloading the table, so pyiceberg's re-append never runs
EVERY_TABLE = {'commit.retry.num-retries': '0'}
#: Table -> its own properties; P's 16 MiB files keep a ticker replace or a compaction from rewriting the whole table
PER_TABLE = {'p_v1': {'write.target-file-size-bytes': str(16 * 1024 * 1024)}}


def main(apply: bool) -> None:
    """Print each table's missing properties, and set them when ``apply``."""
    catalog = load_catalog('default', type='glue', **{'glue.region': 'us-east-1', 's3.region': 'us-east-1'})
    for name in ('bi4_v1', 'bir4_v1', 'bt4_v1', 'p_v1', 'rt4_v1', 'predictions_v1'):
        try:
            table = catalog.load_table(f'secform4strategy.{name}')
        except NoSuchTableError:
            print(f'{name}: no such table yet, skipped')
            continue
        missing = {
            key: value
            for key, value in (EVERY_TABLE | PER_TABLE.get(name, {})).items()
            if table.properties.get(key) != value
        }
        print(f'{name}: {"setting" if apply else "would set"} {missing}' if missing else f'{name}: up to date')
        if apply and missing:
            with table.transaction() as transaction:
                transaction.set_properties(missing)


if __name__ == '__main__':
    main('--apply' in sys.argv[1:])

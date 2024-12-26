from typing import List, Dict, Generator
import pandas as pd
from datetime import datetime
import yfinance as yf
import pyarrow.parquet as pq
import pyarrow as pa
import json
from loguru import logger
import os


class ApiHandler():

    def __init__(
        self,
        transactions: List[Dict],
        mcaps_path: str,
        mapper_path: str,
        failed_mcaps_path: str,
        failed_date_path: str,
        failed_sic_path: str,
        failed_exchange_path: str,
        buffer_size: int 
    ) -> None:

        self.transactions = transactions
        self.mcaps = pd.read_parquet(mcaps_path)
        self.mcaps_path = mcaps_path
        self.mapper = json.load(open(mapper_path))
        self.failed_mcaps_path = failed_mcaps_path
        self.failed_date_path = failed_date_path
        self.failed_sic_path = failed_sic_path
        self.failed_exchange_path = failed_exchange_path
        self.buffer_size = buffer_size

    def get_enriched_data(self) -> Generator[Dict, None, None]:
        
        buffer = []
        for tx in self.transactions:
            tx['market_cap'] = self._get_market_cap(tx)
            tx['exchange'] = self._get_exchange(tx)
            tx['sic'] = self._get_sic(tx)
            
            buffer.append(tx)
           
            if len(buffer) >= self.buffer_size:  
                yield from buffer  
                buffer = []  
               
        #Yield the remaining transactions
        if buffer:
            yield from buffer


    def _get_market_cap(self, tx: dict) -> int:
        """Return the market capitalization of the given ticker at the given filing date.

        If the filing date is after 2024-09-01, it queries Yahoo Finance for the information.
        Otherwise, it looks up the value in the local `mcaps.parquet` file.

        If the ticker is not found in the local file, it appends it to the `failed_mcaps.csv` file.
        """

        # Get the first business day of the month of the transaction date
        try:
            _date = datetime.strptime(tx.get('date'), '%Y-%m-%d').date()
            fbd = pd.date_range(start=_date.replace(
                day=1), periods=1, freq='BMS')[0].date()

        except (ValueError, TypeError):
            self._log_error(tx, self.failed_date_path, 'missing_date')
       
        # If the filing date is recent, query Yahoo Finance & append to the local dataset
        if fbd >= datetime(2024, 9, 1).date():
            ticker = tx.get('ticker')
            mcap = yf.Ticker(ticker.info.get('marketCap'))

            pd.DataFrame(
                [[ticker, mcap, fbd]],
                columns=['ticker', 'mcap', 'date']
            ).to_parquet(self.mcaps_path, engine='pyarrow', append=True)

            return mcap

        # Otherwise, look up the value directly in the local dataset
        try:
            
            row = self.mcaps[
                (self.mcaps['ticker'] == tx.get('ticker').upper()) &
                (self.mcaps['fbd'] == fbd)
            ]
            
            return int(row['marketcap'].iloc[0])

        # In case it fails, record it in the failed dataset
        except:
            self._log_error(tx, self.failed_mcaps_path, 'missing_mcap')


    def _get_exchange(self, tx: dict) -> str:
        try:
            return self.mapper['exchange'].get(tx.get('company_cik', None), None)
        except:
            ticker = yf.Ticker(tx.get('ticker'))
            return ticker.info.get('exchange')
        
        finally:
            self._log_error(tx, self.failed_exchange_path, 'missing_exchange')


    def _get_sic(self, tx: dict) -> str:
        try:
            return self.mapper['sic'].get(tx.get('company_cik', None), None)
        except:
            self._log_error(tx, self.failed_sic_path, 'missing_sic')


    def _log_error(self, tx: dict, path: str, error: str) -> None:
        """Log the error and append the transaction to the corresponding failed dataset."""
        #logger.error(f"Error: {error} for transaction")
        def append_to_parquet(path: str, data: pd.DataFrame) -> None:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                existing_data = pd.read_parquet(path)
                updated_data = pd.concat(
                    [existing_data, data], ignore_index=True)
            else:
                updated_data = data
            updated_data.to_parquet(path, index=False)

        if error == 'missing_mcap':
            _date = datetime.strptime(tx.get('date'), '%Y-%m-%d').date()
            fbd = pd.date_range(start=_date.replace(
                day=1), periods=1, freq='BMS')[0].date()
            failed_data = pd.DataFrame(
                [[tx.get('ticker').upper(), fbd]], columns=['ticker', 'date'])
            append_to_parquet(path, failed_data)

        elif error == "missing_date":
            failed_data = pd.DataFrame(
                [tx.get('ticker').upper()], columns=['ticker'])
            append_to_parquet(path, failed_data)

        elif error in {"missing_sic", "missing_exchange"}:
            failed_data = pd.DataFrame(
                [tx.get('company_cik')], columns=['cik'])
            append_to_parquet(path, failed_data)






import requests
import json
from datetime import datetime
import time
import random
from io import BytesIO
headers = {
        'User-Agent': 'CompanyE InvestmentServices admin@companye.com',
        'Accept-Encoding': 'gzip, deflate',
        'Host': 'www.sec.gov'
    }
import pandas as pd
import os
import sqlite3 as sql




def fetch_parse_data(year, quarter):
    
    url = f'https://www.sec.gov/Archives/edgar/full-index/{year}/{quarter}/master.idx'

    response = requests.get(url, headers = headers, timeout=10)

    if response.status_code == 200:
        content_str = response.content.decode('utf-8')
        lines = content_str.splitlines()

        start_index = 0
        for i, line in enumerate(lines):
            if '|' in line:  
                data_start_index = i + 2
                break

        data_str = '\n'.join(lines[start_index:])

        data = BytesIO(data_str.encode('utf-8'))
        
        # Now read the data into a DataFrame
        df = pd.read_csv(
            data,
            sep='|',
            header=None,
            names=["cik", "company", "form_type", "date", "file_path"],
            dtype=str,
            encoding='utf-8'
        )
        
        return df


def get_historic_data(form_type = '4', years = 5):
   
    all_forms = []
    current_year = datetime.now().year

    for year in range(current_year - years, current_year + 1):
        
        for quarter in ['QTR1', 'QTR2', 'QTR3', 'QTR4']:
            df = fetch_parse_data(year, quarter)
            filings = df[df['Form Type'] == form_type]
            all_forms.append(filings)

    return all_forms


def get_latest_data(form_type = '4'):
    
    current_year = datetime.now().year
    quarter = f'QTR{(datetime.now().month - 1) // 3 + 1}'
    df = fetch_parse_data(current_year, quarter)
    filings = df[df['Form Type'] == form_type]

    return filings

       


if __name__ == '__main__':
    
  

#TODO 
# 1. Stablish connection with redpanda
# 2. Finsish microservice & go to the next one
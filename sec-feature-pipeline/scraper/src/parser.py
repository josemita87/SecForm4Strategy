import re
import requests
import xml.etree.ElementTree as ET
from transaction import EssentialTrade
from typing import Text, List, Dict
import time
from loguru import logger
# Get the absolute path to the 'scraper' directory


from const import headers, root, derivative_root, insider_paths, paths, derivative_paths


class Form4Parser:
    BASE_URL = 'https://www.sec.gov/Archives/'
    XML_DELIM = r'<XML>\s*(<\?xml[^>]+?>.*?)</XML>'
    ROOT = root
    DERIVATIVE_ROOT = derivative_root
    INSIDER_PATHS = insider_paths
    PATHS = paths
    DERIVATIVE_PATHS = derivative_paths
    HEADERS = headers

    def __init__(self,  url, sleep_time=0.1):
        self.url = self.BASE_URL + url
        logger.info(self.url)
        time.sleep(sleep_time)
        self.xml = self.get_filing()

    def get_filing(self) -> Text:
        try:
            filing = requests.get(self.url, headers=headers).text
            return ET.fromstring(re.search(self.XML_DELIM, filing, re.DOTALL).group(1))
        
        except Exception as e:
            logger.error(f"\n\nFailed to get filing: {e}, {self.url}\n\n")
            time.sleep(5)
            return None
    def create_txs(self) -> list[dict]:
        '''For a given filing, create a list of transactions.
        Initialize list to store transactions as dicts, and query the XML for 
        non-derivative and derivative transactions.For each individual transaction, 
        create EssentialTrade which is filled with process_transaction().'''
        
        filing_data = []

        if self.xml is None:
            return None

        params = [
            (self.ROOT, self.PATHS),
            (self.DERIVATIVE_ROOT, self.DERIVATIVE_PATHS)
        ]

        # Get both non-derivative and derivative transactions
        for root, paths in params:

            # Find all transactions in the filing for the given root (derivative or non-derivative)
            for etree in self.xml.findall(root):

                self.transaction = EssentialTrade(link=self.url, xml=self.xml)
                filing_data.append(
                    self._process_transaction(
                        etree,
                        paths,
                    )
                )

        return filing_data

    def _process_transaction(self, etree: ET.ElementTree, paths: dict) -> dict:

        self._query_xml(self.xml, self.INSIDER_PATHS)
        self._query_xml(etree, paths)

        return self.transaction.to_dict()

    def _query_xml(self, etree: ET.ElementTree, paths: dict):
        """
        Given an ElementTree object and a dictionary of paths, 
        this function queries the paths and sets the attributes 
        of the transaction object.

        Parameters:
            etree (ElementTree): The ElementTree object to query.
            paths (dict): A dictionary where the keys are the desired output keys, 
            and the values are the paths to the values in the ElementTree.
        Returns:
            None
        """

        for key, path in paths.items():

            try:
                element = etree.find(path)

                if element is not None:
                    if 'footnote_id' in key.lower():
                        setattr(self.transaction, key, element.get('id'))

                    else:
                        setattr(self.transaction, key, element.text)

            except AttributeError:
                pass

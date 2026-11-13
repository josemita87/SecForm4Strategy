"""Entry point: consumes filing URLs, parses their Form 4s, and produces the transactions."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from config import config
from parser import parse_filing
from secform4strategy_clients.edgar import EdgarClient
from secform4strategy_clients.messaging import KafkaClient
from secform4strategy_clients.ports import Filing, PermanentError, RetryableError

if TYPE_CHECKING:
    from secform4strategy_clients.ports import Filings, Message, MessageQueue

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)

# httpx logs every SEC request at INFO; progress is logged per batch instead
logging.getLogger('httpx').setLevel(logging.WARNING)


def read_filing(message: Message, max_age: timedelta | None) -> Filing | None:
    """Return the filing a master-index message carries, or None if the message is malformed or the filing stale.

    Args:
        message: A message received from the master-index queue.
        max_age: Filings older than this are stale; None keeps every filing.

    Returns:
        The filing to parse, or None (logged) to skip it.
    """
    body = message.body
    try:
        filing = Filing(body['accession'], body['file_path'], datetime.fromisoformat(body['filed_at']))
    except KeyError, TypeError, ValueError, OverflowError, OSError:
        logger.error(f'Malformed filing message: {body}')
        return None
    if max_age is not None and filing.filed_at < datetime.now(UTC) - max_age:
        logger.debug(f'Filing too old: {filing.filed_at}')
        return None
    return filing


def scrape(
    messages: tuple[Message, ...], filings: Filings, queue: MessageQueue, output: str, max_age: timedelta | None
) -> tuple[list[Message], int]:
    """Parse the messages' filings in order, then send all their transactions to ``output`` at once.

    Parsing stops at the first filing that fails with ``RetryableError``: the failure most likely
    affects the filings after it too, so it and every later message are left unfinished and
    delivered again. A filing that fails with ``PermanentError`` is logged and finished, as are
    skipped messages.

    Args:
        messages: Messages received from the master-index queue.
        filings: SEC filings source.
        queue: Where the transactions are sent.
        output: Queue the transactions are sent to.
        max_age: Filings older than this are skipped; None keeps every filing.

    Returns:
        The finished messages, a prefix of ``messages``, and the number of transactions sent.
    """
    done: list[Message] = []
    txs: list[dict[str, object]] = []
    for message in messages:
        try:
            txs.extend(parse_filing(filing, filings) if (filing := read_filing(message, max_age)) else ())
        except RetryableError as error:
            logger.warning(f'{len(messages) - len(done)} filings left for redelivery: {error}')
            break
        except PermanentError as error:
            logger.error(f'Filing dropped: {error}')
        done.append(message)
    if txs:
        queue.send(output, ((str(r['key']), r) for r in txs))
    return done, len(txs)


if __name__ == '__main__':
    queue: MessageQueue = KafkaClient(
        broker_address=config.kafka_broker_address,
        consumer_group=config.consumer_group,
        auto_offset_reset=config.auto_offset_reset,
    )
    edgar: Filings = EdgarClient(config.sec_user_agent, config.sec_max_rate)

    time.sleep(config.delay)
    logger.info(f'Scraper started: {config.kafka_input_topic} -> {config.kafka_output_topic}')

    while True:
        messages = queue.receive(config.kafka_input_topic, config.buffer_size, config.poll_timeout)
        done, sent = scrape(messages, edgar, queue, config.kafka_output_topic, config.max_age)
        # Acked after every receive, an empty one included, as the port requires
        queue.ack(config.kafka_input_topic, done)
        if messages:
            logger.info(f'{len(done)} of {len(messages)} filings -> {sent} transactions')
        if len(done) < len(messages):
            time.sleep(config.retry_pause)

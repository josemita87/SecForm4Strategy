"""Records this service writes: one prediction per scored row, as the ``predictions`` feature group stores it."""

from constants import OrderOutcome, Source
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field


class Prediction(BaseModel):
    """One BIR4 row scored by one model version on one path, with the order it led to.

    ``key`` is unique per source, model version and row, so a rerun replaces its record and a new model
    adds one. A short's client order id is its row key, so the Alpaca order joins on it.
    """

    model_config = ConfigDict(frozen=True, use_enum_values=True)

    row_key: str
    ticker: str
    accepted_at: AwareDatetime
    probability: float = Field(ge=0, le=1)
    probability_cutoff: float = Field(gt=0, lt=1)
    model_version: str
    source: Source
    scored_at: AwareDatetime
    #: The Lambda request id of a live run; None for a batch run
    run_id: str | None
    #: None when the row was not shorted
    order_outcome: OrderOutcome | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        """The record's identity: ``<source>|<model version>|<row key>``."""
        return f'{self.source}|{self.model_version}|{self.row_key}'

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shorted(self) -> bool:
        """Whether the probability reached the cutoff."""
        return self.probability >= self.probability_cutoff

    @computed_field  # type: ignore[prop-decorator]
    @property
    def client_order_id(self) -> str | None:
        """The short's client order id, or None when the row was not shorted."""
        return self.row_key if self.shorted else None

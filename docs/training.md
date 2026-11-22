# Training

The training service (`training/src/main.py`) fits a `HistGradientBoostingRegressor` that predicts each filing's 30-day return (`pct_change`). It picks the model parameters by walk-forward cross-validation, then scores the chosen model once on a holdout set.

## Settings

| Variable | Value | Meaning |
| --- | --- | --- |
| `HOLDOUT_RATIO` | `0.2` | Share of the latest rows held out for the final score |
| `N_FOLDS` | `4` | Number of walk-forward validation folds on the development set |
| `PREDICTION_THRESHOLD` | `-0.05` | The evaluation shorts rows predicted below this return. Inference trades at the same variable and value. |
| `RANDOM_STATE` | `42` | Seed of the regressor |
| `TARGET_FEATURE` | `pct_change` | The label column |

`training/src/spec.yaml` holds three model sections:

- `model_params`: the fixed regressor parameters, `max_iter=500`, `early_stopping=true` and `validation_fraction=0.1`.
- `param_grid`: the parameters the grid search tries on top of `model_params`.
- `report_thresholds`: the thresholds at which training logs the holdout short return. Training saves them in the model metadata, and the site's `model.json` reports the holdout at the same thresholds.

## Splits

Training sorts the rows by `accepted_at` and splits them in time:

```
accepted_at ──────────────────────────────────────────────────────────────►

|<──────────────── development (80%) ────────────────>|<── holdout (20%) ──>|
| block 0 | block 1 | block 2 | block 3 | block 4      ^ holdout_start
            fold 1    fold 2    fold 3    fold 4

fold 1:  train = block 0              validate = block 1
fold 2:  train = blocks 0-1           validate = block 2
fold 3:  train = blocks 0-2           validate = block 3
fold 4:  train = blocks 0-3           validate = block 4
final:   train = blocks 0-4           score    = holdout
```

- **Holdout:** the latest `HOLDOUT_RATIO` of rows. `holdout_start` is the `accepted_at` of its first row.
- **Development:** the earlier rows whose `label_known_at` is before `holdout_start`.
- **Blocks:** training cuts the development set into `N_FOLDS + 1` consecutive time blocks.
- **Folds:** fold k validates on block k. It trains on every development row whose `label_known_at` is before block k starts.

### Purging

A label is the return to the first close at least 30 calendar days after the filing. `label_known_at` is the time of that close. A row filed 10 days before `holdout_start` has a label that uses prices from inside the holdout period. Training drops such rows from the development set, and from each fold's training rows for the same reason. This rule is called purging. Without it, the model trains on prices from the period it is scored on.

## Grid search

scikit-learn `GridSearchCV` fits every parameter combination on every fold, with `n_jobs=-1`.

| Parameter | Values |
| --- | --- |
| `loss` | `squared_error`, `absolute_error` |
| `learning_rate` | `0.03`, `0.1` |
| `max_leaf_nodes` | `15`, `31` |
| `min_samples_leaf` | `50`, `200` |
| `l2_regularization` | `0`, `1` |

The grid has 32 combinations. With 4 folds, that is 128 fits. Each fit uses early stopping with a cap of `max_iter=500`.

`GridSearchCV` selects the combination with the lowest mean validation MAE (mean absolute error). The short return is not the selection metric. At the traded threshold, each fold's short return comes from a few hundred trades (265 on the 1-year holdout), which is too noisy to rank 32 combinations.

## Final model and holdout

Training refits the best combination on the whole development set. It then scores the refitted model once on the holdout, next to three baselines:

| Predictor | Prediction |
| --- | --- |
| `model` | The refitted pipeline |
| `zero` | No move (0 for every row) |
| `train_mean` | The mean development label |
| `ticker_history` | `avg_target_expanding`, the ticker's past average return, or the development mean where it is missing |

Each predictor gets these scores:

- `mae`: the mean absolute error of the predicted return.
- `short_return`: the mean return captured per short. The predictor shorts every row it predicts below `PREDICTION_THRESHOLD`. Positive is a profit.
- `n_shorts`: the number of rows shorted.

Training also logs the model's short return at each of `report_thresholds`: 0, -2%, -5% and -10%.

The holdout decides nothing. The parameters, the threshold and the feature set are all fixed before training scores the holdout. If a holdout result leads to a change and a new run, the holdout no longer measures unseen data.

## Logs and metadata

Training logs:

- the row counts and time range of the development set, each fold and the holdout;
- the best parameters;
- the per-fold validation MAE of the best combination, next to the zero baseline's MAE on the same fold;
- the holdout scores of the model and each baseline;
- the model's holdout short return at each threshold.

`model.meta.json` saves the `ModelMetadata` record (`secform4strategy_clients.records`), with these fields among the others:

| Field | Content |
| --- | --- |
| `model_params` | The fixed parameters and the best grid combination |
| `train_rows`, `test_rows`, `test_start` | The development and holdout row counts, and `holdout_start` |
| `validation_mae` | The best combination's validation MAE per fold |
| `validation_zero_mae` | The zero baseline's MAE per fold |
| `prediction_threshold` | The threshold used to score the holdout |
| `report_thresholds` | The `report_thresholds` of `spec.yaml`; empty in models saved before training recorded them |
| `metrics` | The holdout scores of the model and its baselines |

[configuration.md](configuration.md) lists the rest of the record.

## Run time

The ECS task has 4 vCPU and 16 GB of memory. On the 1-year data (73,195 rows), the 128 fits plus the refit used about 2,200 CPU-seconds: 3 minutes on a 16-core laptop. Fit time grows about linearly with rows, so the 5-year data (about 400,000 rows) should take 45 to 60 minutes on the task. Early stopping makes each fit's time depend on the parameters; a smaller `param_grid` shortens the run.

## Limits

- **One holdout window.** The holdout score comes from one period, the latest 20% of rows. A different period can give a different result.
- **Random early-stopping rows.** Early stopping holds out a random 10% of each fit's training rows, not the latest 10%. Those rows are close in time to the rows the fit trains on, so early stopping can stop later than a time-ordered split would.

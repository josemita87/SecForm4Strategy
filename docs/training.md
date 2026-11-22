# Training

The training service (`training/src/main.py`) fits a `HistGradientBoostingRegressor` that predicts each filing's 30-day return (`pct_change`). It picks the model parameters by walk-forward cross-validation, then scores the chosen model once on a holdout set.

## Settings

| Variable | Value | Meaning |
| --- | --- | --- |
| `HOLDOUT_RATIO` | `0.2` | Share of the latest rows held out for the final score |
| `N_FOLDS` | `4` | Number of walk-forward validation folds on the development set |
| `RANDOM_STATE` | `42` | Seed of the classifier |
| `TARGET_FEATURE` | `pct_change` | The label column |

`training/src/spec.yaml` holds these model sections:

- `short_return`: the label. A row is a short winner when its `pct_change` is below `-0.05`.
- `model_params`: the fixed classifier parameters, `max_iter=500`, `early_stopping=true` and `validation_fraction=0.1`.
- `param_grid`: the parameters the grid search tries on top of `model_params`.
- `cutoff_grid`: the probability cutoffs from 0.30 to 0.80 in steps of 0.05. Training chooses the cutoff from this grid on the development folds and reports the holdout shorts at each cutoff. The site's `model.json` reports the holdout at the same cutoffs.
- `min_fold_shorts`: a cutoff qualifies only when it shorts at least 100 rows in every fold.
- `fallback_cutoff`: the cutoff saved when no cutoff qualifies, `0.5`.

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

`GridSearchCV` selects the combination with the lowest mean validation MAE (mean absolute error). The short return is not the selection metric. At the traded threshold, each fold's short return comes from a few hundred trades (265 on the 1-year holdout, 619 on the 5-year holdout), which is too noisy to rank 32 combinations.

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
| `probability_cutoff` | The cutoff chosen on the development folds; inference shorts rows whose probability is at least this value |
| `label_return` | The `short_return` of `spec.yaml` |
| `base_rate` | The holdout's share of short winners, which is also the average precision of a constant predictor |
| `validation_ap` | The best combination's validation average precision per fold |
| `validation_base_rate` | Each fold's share of short winners in its validation rows |
| `cutoff_sweep` | The holdout shorts at each cutoff of `cutoff_grid`: `cutoff`, `n_shorts`, `mean_return`, `median_return` |
| `metrics` | The holdout scores of the model and its baselines |

Models saved before the classifier have none of these fields except `metrics`. They still load, so the promotion gate can compare a new model with them.

[configuration.md](configuration.md) lists the rest of the record.

## Promotion

Training saves every model as a new version. Inference loads only the promoted version: the folder that `latest.json` names in S3, or the file at `MODEL_STORE__PATH` locally. A saved model that is not promoted changes nothing that trades.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROMOTE` | `never` | `never` saves the model and does not promote it. `gated` promotes it when every gate check passes. |
| `MIN_TRAIN_ROWS` | `50000` | The fewest development rows (`train_rows`) a promoted model may have |
| `MIN_ROWS_RATIO` | `0.8` | The smallest share of the current model's `train_rows` a promoted model may have |

The `retrain` task definition sets `PROMOTE=gated`, `MIN_TRAIN_ROWS=50000` and `MIN_ROWS_RATIO=0.8`, so the weekly schedule promotes through the gate. The local default is `never`.

### Gate checks

`promotion.gate` (`training/src/promotion.py`) compares the new model's metadata with the promoted model's metadata. It promotes only when every check passes:

| Check | Passes when |
| --- | --- |
| `min_train_rows` | `train_rows >= MIN_TRAIN_ROWS` |
| `train_rows_vs_current` | `train_rows >= MIN_ROWS_RATIO * current.train_rows` |
| `feature_group_version` | The feature-group version equals the current model's |
| `features` | The `features` tuple, names and order, equals the current model's |
| `test_rows` | `test_rows > 0` |
| `holdout_average_precision` | The model's holdout average precision is finite |
| `n_shorts` | The model shorts at least one holdout row at `probability_cutoff` |

If no model is promoted yet, the gate skips the three checks that compare with the current model. `train_rows_vs_current` catches a truncated dataset, for example a run on 1 day of data. `features` and `feature_group_version` catch a model whose inputs differ from the rows that inference builds.

The gate does not compare the holdout MAE with the current model's MAE. Each run holds out the latest 20% of its own rows, so two models are scored on different periods. A lower MAE on one period does not show that a model is better than a model scored on another period.

### Logs

Training logs the saved version. Under `never`, it logs that the model was saved and not promoted, and the `scripts/promote_model.sh` command that promotes it. Under `gated`, it logs one line per check, `pass` or `FAIL` with its values, and then whether the model was promoted. A model that fails the gate stays saved, and the previous model stays promoted. The metadata does not record the decision, because training writes the metadata before the gate runs.

### Manual promotion and rollback

`scripts/promote_model.sh` uses the AWS profile `secform4` unless `AWS_PROFILE` is set. It reads the bucket `secform4strategy-data-<account>`, or `MODELS_BUCKET` when set.

1. Run `scripts/promote_model.sh` without an argument. It prints each saved version with `trained_at`, `train_rows`, the model's holdout average precision, the holdout base rate, the probability cutoff, and `n_shorts`. It marks the promoted version with `<- promoted`.
2. Run `scripts/promote_model.sh <version>`. It checks that the version folder holds `model.meta.json` and `model.joblib`, then writes `{"version": "<version>"}` to `latest.json`.

To roll back, promote an earlier version the same way. Inference loads the model at each invocation, so the next invocation uses the promoted version.

## Run time

The task definition in `infra/envs/demo/main.tf` gives training 4 vCPU and 16 GB of memory. Early stopping makes each fit's time depend on the parameters; a smaller `param_grid` shortens the run.

| Data | Rows | Where | Run time |
| --- | --- | --- | --- |
| 1 year | 73,195 | A 16-core laptop | About 2,200 CPU-seconds for the 128 fits plus the refit: 3 minutes |
| 5 years | 215,334 | ECS on AWS Fargate, revision 11, overridden to 16 vCPU and 32 GB, `PROMOTE=never` | About 10 minutes, from 20:48 to 20:58 UTC on 2026-11-22 |

The 5-year run overrode the configured size at run time. Container Insights shows it used all 16 vCPU at 100% and peaked at about 20 GB of memory, because each `n_jobs` worker holds a copy of the data. The configured 4 vCPU and 16 GB are therefore enough for the weekly runs. The override only shortened the run, from an expected 35 minutes at 4 vCPU to about 10 minutes.

The 5-year run was the first `training` run on AWS Fargate. It saved model `20261122T205816Z`, and `scripts/promote_model.sh 20261122T205816Z` promoted it by hand in place of `20261120T210617Z`.

## 5-year results

The 5-year training data has 215,334 rows and 34 columns. The development set has 169,990 rows after purging. The holdout has 43,067 rows and starts at 2025-10-24 18:24 UTC. The best parameters are `loss=absolute_error`, `learning_rate=0.03`, `max_leaf_nodes=15`, `min_samples_leaf=200` and `l2_regularization=0.0`.

Each fold validates on 33,998 rows. The model is worse than zero in every fold:

| Fold | Training rows | Validation period | Model MAE | Zero MAE |
| --- | --- | --- | --- | --- |
| 1 | 30,472 | 2021-11-22 to 2022-12-21 | 0.11050 | 0.10804 |
| 2 | 64,675 | 2022-12-21 to 2023-12-19 | 0.11192 | 0.11030 |
| 3 | 97,893 | 2023-12-19 to 2024-11-14 | 0.10011 | 0.09908 |
| 4 | 133,002 | 2024-11-14 to 2025-09-23 | 0.11330 | 0.11307 |

Holdout scores at `PREDICTION_THRESHOLD` = -0.05:

| Predictor | MAE | Short return | Shorts |
| --- | --- | --- | --- |
| `model` | 0.12653 | +2.64% | 619 |
| `zero` | 0.12646 | 0 | 0 |
| `train_mean` | 0.12657 | 0 | 0 |
| `ticker_history` | 0.13548 | -6.42% | 2,982 |

The model's holdout short return at each of `report_thresholds`:

| Threshold | Shorts | Mean return captured |
| --- | --- | --- |
| 0 | 28,356 | -1.44% |
| -0.02 | 6,602 | -3.24% |
| -0.05 | 619 | +2.64% |
| -0.10 | 51 | +5.04% |

- The model has no MAE edge over predicting zero, on the holdout or on any fold.
- The holdout profit sits only in the extreme tail. The 619 shorts at -0.05 are 1.4% of the holdout, and only 51 rows fall below -0.10.
- The return does not grow steadily with the threshold: -0.02 loses while -0.05 gains. That pattern is consistent with noise.
- `ticker_history` loses 6.42% per short, so the naive baseline fails clearly.
- The returns exclude borrow fees, slippage and commissions.

The 1-year holdout MAE (0.1216) and the 5-year holdout MAE are not comparable, because each model holds out a different period. For the same reason, the promotion gate does not compare MAE.

## Limits

- **One holdout window.** The holdout score comes from one period, the latest 20% of rows. A different period can give a different result.
- **Random early-stopping rows.** Early stopping holds out a random 10% of each fit's training rows, not the latest 10%. Those rows are close in time to the rows the fit trains on, so early stopping can stop later than a time-ordered split would.

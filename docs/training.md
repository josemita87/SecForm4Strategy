# Training

The training service (`training/src/main.py`) fits a `HistGradientBoostingClassifier` that gives each row the probability of a short winner. A short winner is a row whose 30-day return (`pct_change`) is below `-0.05`: a drop of more than 5%. Training picks the model parameters by walk-forward cross-validation, chooses a probability cutoff on the same folds, then scores the chosen model once on a holdout set.

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

scikit-learn `GridSearchCV` fits every parameter combination on every fold, with `n_jobs=-1`. The classifier learns the binary label `pct_change < short_return`.

| Parameter | Values |
| --- | --- |
| `learning_rate` | `0.03`, `0.1` |
| `max_leaf_nodes` | `15`, `31` |
| `min_samples_leaf` | `50`, `200` |
| `l2_regularization` | `0`, `1` |
| `class_weight` | `null`, `balanced` |

The grid has 32 combinations. With 4 folds, that is 128 fits. Each fit uses early stopping with a cap of `max_iter=500`.

`GridSearchCV` selects the combination with the highest mean validation average precision (AP). AP measures how well the probabilities rank the short winners above the other rows. A constant predictor has an AP equal to the base rate: the share of short winners in the rows.

## Probability cutoff

Training chooses the cutoff from the development folds only. The holdout plays no part in it.

1. Training refits the best combination on each fold's training rows and predicts that fold's validation rows (`model.fold_probabilities`).
2. For each cutoff in `cutoff_grid`, it shorts every validation row whose probability is at least the cutoff, and pools the shorts of all folds.
3. A cutoff qualifies only when every fold has at least `min_fold_shorts` shorts.
4. Training chooses the qualifying cutoff with the highest median captured return. The lowest cutoff wins a tie. The median keeps a few extreme returns from deciding the cutoff.
5. If no cutoff qualifies, training saves `fallback_cutoff`.

The captured return of a short is minus the row's `pct_change`, so a positive value is a profit. The metadata stores the cutoff as `probability_cutoff`. A promotion therefore moves the model and its cutoff together. Inference shorts a row when its probability is at least this cutoff, and refuses a model without one. The optional `PROBABILITY_CUTOFF` variable of inference replaces the saved cutoff.

## Final model and holdout

Training refits the best combination on the whole development set. It then scores the refitted model once on the holdout, next to two baselines:

| Predictor | Probability |
| --- | --- |
| `model` | The refitted pipeline |
| `base_rate` | The development share of short winners, for every row |
| `ticker_history` | The ticker's share of short winners among its labels known before the row's `accepted_at`, or the development share where it has none |

Each predictor shorts the rows at or above `probability_cutoff` and gets these scores (`Scores` in `secform4strategy_clients.records`):

- `average_precision` and `roc_auc`: how well the probabilities rank the short winners.
- `precision`: the share of short winners among the shorts.
- `n_shorts`: the number of rows shorted.
- `short_return`, `median_return` and `trimmed_return`: the mean, median and 5% trimmed mean of the captured returns.

Training also reports the model's holdout shorts at each cutoff of `cutoff_grid`, with their number, mean and median captured return.

The holdout decides nothing. The parameters, the cutoff and the feature set are all fixed before training scores the holdout. If a holdout result leads to a change and a new run, the holdout no longer measures unseen data.

## Logs and metadata

Training logs:

- the row counts and time range of the development set, each fold and the holdout;
- the best parameters;
- the per-fold validation AP of the best combination, next to the fold's base rate;
- each cutoff's fold shorts, the fewest shorts in a fold, and the median captured return;
- the chosen cutoff and the holdout base rate;
- the holdout scores of the model and each baseline;
- the model's holdout shorts at each cutoff.

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

The gate does not compare the holdout AP with the current model's AP. Each run holds out the latest 20% of its own rows, so two models are scored on different periods. A higher AP on one period does not show that a model is better than a model scored on another period.

### Logs

Training logs the saved version. Under `never`, it logs that the model was saved and not promoted, and the `scripts/promote_model.sh` command that promotes it. Under `gated`, it logs one line per check, `pass` or `FAIL` with its values, and then whether the model was promoted. A model that fails the gate stays saved, and the previous model stays promoted. The metadata does not record the decision, because training writes the metadata before the gate runs.

### Manual promotion and rollback

`scripts/promote_model.sh` uses the AWS profile `secform4` unless `AWS_PROFILE` is set. It reads the bucket `secform4strategy-data-<account>`, or `MODELS_BUCKET` when set.

1. Run `scripts/promote_model.sh` without an argument. It prints each saved version with `trained_at`, `train_rows`, the model's holdout average precision, the holdout base rate, the probability cutoff, and `n_shorts`. It marks the promoted version with `<- promoted`.
2. Run `scripts/promote_model.sh <version>`. It checks that the version folder holds `model.meta.json` and `model.joblib`, then writes `{"version": "<version>"}` to `latest.json`.

To roll back, promote an earlier version the same way. Inference loads the model at each invocation, so the next invocation uses the promoted version.

## Run time

The task definition in `infra/envs/demo/main.tf` gives training 4 vCPU and 16 GB of memory. Early stopping makes each fit's time depend on the parameters; a smaller `param_grid` shortens the run.

| Run | Rows | Where | Run time |
| --- | --- | --- | --- |
| 5-year classifier | 215,273 | ECS on AWS Fargate, revision 13, overridden to 16 vCPU and 32 GB, `PROMOTE=never` | About 16 minutes, from 22:33 to 22:49 UTC on 2026-11-22 |

The run saved model `20261122T224917Z` at git `303401c`. `scripts/promote_model.sh` promoted it by hand in place of `20261122T205816Z`, the 5-year regressor that the classifier replaced.

The earlier 5-year regressor run used the same override. Container Insights showed it used all 16 vCPU at 100% and peaked at about 20 GB of memory, because each `n_jobs` worker holds a copy of the data. The configured 4 vCPU and 16 GB are therefore enough for the weekly runs. The override only shortens the run.

## 5-year results

target rebuilt `RT4` with the label guards ([configuration.md](configuration.md), `max_label_return`): 215,273 labelled rows. The development set has 169,941 rows after purging. The holdout has 43,055 rows and starts at 2025-10-24 18:24 UTC. The best parameters are `class_weight=balanced`, `l2_regularization=0.0`, `learning_rate=0.03`, `max_leaf_nodes=15` and `min_samples_leaf=50`.

Each fold validates on 33,988 or 33,989 rows. The model's AP is above the base rate in every fold:

| Fold | Training rows | Validation period | Model AP | Base rate |
| --- | --- | --- | --- | --- |
| 1 | 30,456 | 2021-11-22 to 2022-12-21 | 0.4757 | 0.4105 |
| 2 | 64,648 | 2022-12-21 to 2023-12-19 | 0.3748 | 0.3074 |
| 3 | 97,859 | 2023-12-19 to 2024-11-14 | 0.3635 | 0.2538 |
| 4 | 132,963 | 2024-11-14 to 2025-09-23 | 0.4084 | 0.3582 |

The median captured return of the pooled fold shorts at each cutoff:

| Cutoff | 0.30 | 0.40 | 0.50 | 0.55 | 0.60 | 0.65 | 0.70 | 0.75, 0.80 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Median | +0.59% | +0.69% | +1.13% | +1.77% | +2.48% | +3.75% | +5.53% | Too few shorts |

0.70 takes 3,343 fold shorts, with 295 in the fold with the fewest. It is the chosen cutoff.

The holdout base rate is 0.3382. Holdout scores at `probability_cutoff` = 0.70:

| Predictor | AP | ROC-AUC | Precision | Shorts | Mean | Median | 5% trimmed mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `model` | 0.4184 | 0.5892 | 0.5987 | 314 | +9.58% | +9.89% | +11.45% |
| `ticker_history` | 0.4042 | 0.5730 | 0.5190 | 1,025 | +5.82% | +5.91% | +7.06% |
| `base_rate` | 0.3383 | 0.5000 | – | 0 | – | – | – |

The model's holdout shorts at each cutoff:

| Cutoff | Shorts | Mean | Median |
| --- | --- | --- | --- |
| 0.30 | 41,650 | -0.39% | +0.27% |
| 0.35 | 40,059 | -0.41% | +0.29% |
| 0.40 | 36,227 | -0.40% | +0.39% |
| 0.45 | 29,790 | -0.29% | +0.62% |
| 0.50 | 21,350 | -0.13% | +0.94% |
| 0.55 | 12,141 | +0.48% | +2.01% |
| 0.60 | 5,653 | +1.58% | +2.93% |
| 0.65 | 1,647 | +5.66% | +6.99% |
| 0.70 | 314 | +9.58% | +9.89% |
| 0.75 | 52 | +20.51% | +16.36% |
| 0.80 | 1 | +49.75% | +49.75% |

- **Signal.** The model ranks short winners modestly but consistently. Its AP is above the base rate in all 4 folds and on the holdout (0.418 against 0.338). Its holdout ROC-AUC is 0.589.
- **Consistency.** The median return rises with the cutoff on the folds and on the holdout. At 0.70 the mean and the median agree (+9.6% and +9.9%), so single outliers do not drive the result.
- **Baseline.** `ticker_history` reaches AP 0.404, so much of the signal is per-ticker persistence. The model's advantage is at the top: precision 0.60 against 0.52, and a median of +9.9% against +5.9%.
- **Costs.** The returns exclude borrow fees, slippage and commissions. The shorted names are likely small caps that are hard to borrow, and Alpaca shorts only easy-to-borrow names.
- **Sample.** 314 shorts in one holdout year is about 1.2 per trading day. Their 30-day windows overlap, so the effective sample is smaller.

### Superseded regressor

The classifier replaced a `HistGradientBoostingRegressor` of `pct_change`. Neither the 1-year nor the 5-year regressor beat a constant zero prediction on mean absolute error (MAE). The 1-year holdout MAE was 0.1216 against 0.1193 for zero. The 5-year model `20261122T205816Z` had a holdout MAE of 0.12653 against 0.12646, and was worse than zero in all 4 folds. Single outlier labels drove its holdout mean short return. These results led to the label guards and to the classifier.

## Limits

- **One holdout window.** The holdout score comes from one period, the latest 20% of rows: about one year. A different period can give a different result.
- **Feature skew in live scoring.** The live cluster features read `BI4` only, which has a shorter history than `BT4`. The fix is to read `BT4` and `BI4` together.
- **Random early-stopping rows.** Early stopping holds out a random 10% of each fit's training rows, not the latest 10%. Those rows are close in time to the rows the fit trains on, so early stopping can stop later than a time-ordered split would.

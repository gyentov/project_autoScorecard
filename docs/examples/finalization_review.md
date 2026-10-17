# Finalization review

Build `20261018-012322-b891ae`: **DRAFT, waiting for your confirmation**.

## Forced features

Tried in the final model regardless of the earlier steps; discarded only if they still fail a finalization check inside the model.

| feature | previously_dropped_at | previous_reason | result | reason |
|---|---|---|---|---|
| cust_province | Stepwise selection | not entered: LR p=0.06143 >= 0.01 given the selected features | discarded | forced into the model but still fails: p-value 0.0674 >= 0.05 |
| cust_years_at_job | Information Value | IV 0.0104 <= 0.02 | discarded | forced into the model but still fails: p-value 0.1024 >= 0.05 |

## Manual edits

### cust_age: bins set by hand (in the final model)

Edit: `{'edges': [25, 35, 45, 55]}` · reason: align with the policy age bands · after the edit the bad rate is **NOT monotonic**.

Before:

| bin | count | bad_rate | woe |
|---|---|---|---|
| (-inf, 29] | 757 | 0.1612 | -0.4986 |
| (29, 34] | 420 | 0.1548 | -0.4504 |
| (34, 43] | 733 | 0.1078 | -0.03451 |
| (43, 54] | 896 | 0.08147 | 0.2743 |
| (54, 60] | 539 | 0.05566 | 0.6831 |
| (60, inf] | 320 | 0.04375 | 0.9364 |

After:

| bin | count | bad_rate | woe |
|---|---|---|---|
| (-inf, 25] | 415 | 0.1494 | -0.4088 |
| (25, 35] | 832 | 0.1538 | -0.4434 |
| (35, 45] | 792 | 0.1073 | -0.02979 |
| (45, 55] | 847 | 0.08383 | 0.2433 |
| (55, inf] | 779 | 0.0475 | 0.8503 |

Confirm with `POST /api/projects/{id}/finalization/confirm` (`{"build_id": "20261018-012322-b891ae"}`). Changing the edits or forced features needs a rebuild before confirming.

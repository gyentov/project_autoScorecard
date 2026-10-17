# Data dictionary

Rows: oot 764, test 1,571, train 3,665 · columns: 27.

**Outlier detection:** IQR (Tukey) fences: a value is an outlier if it is below Q1 − 1.5×IQR or above Q3 + 1.5×IQR, with Q1/Q3 the 25th/75th percentiles of the training sample. Numeric columns with 10 or fewer distinct values are not tested.

**Outlier treatment:** none: values are kept as they are. Binning makes this safe: the first and last bins are open-ended, so an extreme value only receives the WoE of its end bin and cannot pull the model; the same holds for new data at scoring time.

**To complete:** 3 variable(s) without a description, 1 with a guessed variable type. Upload a data dictionary (columns: variable, description, category, logic) to fill them.

## Variables

| variable | description | variable_type | role | data_type | logic |
|---|---|---|---|---|---|
| app_id | Application number | identifier | not used | text | raw: applications.app_id |
| cust_id | Customer number (CIF) | identifier | not used | text | raw: applications.cust_id |
| app_date | Date the application was submitted | application | not used | date | raw: applications.app_date |
| loan_amount | Requested loan amount (IDR) | loan | in final model | integer (stored as decimal) | raw: applications.loan_amount |
| tenor_months | Requested tenor in months | loan | in final model | integer | raw: applications.tenor_months |
| channel | Channel the application came through | application | in final model | text | raw: applications.channel |
| is_staff_loan | 1 if the applicant is a bank employee | loan | candidate feature | integer | raw: applications.is_staff_loan |
| noise_a | (not provided) | (not provided) | candidate feature | decimal | raw: applications.noise_a |
| noise_b | (not provided) | (not provided) | candidate feature | text | raw: applications.noise_b |
| cust_age | Age at application (years) | demographic | in final model | integer | customers.age; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_income | Declared monthly income (IDR) | income & employment | in final model | integer (stored as decimal) | customers.income; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_income_band | Declared monthly income band | income & employment | candidate feature | text | income cut at 4, 7 and 12 million IDR; then customers.income_band; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_marital_status | Marital status | demographic | candidate feature | text | customers.marital_status; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_province | Province of residence | demographic | candidate feature | text | customers.province; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_years_at_job | Years with current employer | income & employment | candidate feature | decimal | customers.years_at_job; left join customers on cust_id = cust_id; missing when the key has no customers record |
| cust_spouse_income | Declared monthly income of spouse (IDR) | income & employment | candidate feature | integer (stored as decimal) | customers.spouse_income; left join customers on cust_id = cust_id; missing when the key has no customers record |
| bur_count | (not provided) | bureau / credit history (guessed) | candidate feature | integer (stored as decimal) | number of bureau rows per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_amount_mean | Mean of: amount of a previous loan at any bank (IDR) | bureau / credit history | candidate feature | decimal | mean of bureau.prev_amount per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_amount_max | Maximum of: amount of a previous loan at any bank (IDR) | bureau / credit history | candidate feature | integer (stored as decimal) | maximum of bureau.prev_amount per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_amount_min | Minimum of: amount of a previous loan at any bank (IDR) | bureau / credit history | candidate feature | integer (stored as decimal) | minimum of bureau.prev_amount per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_amount_sum | Sum of: amount of a previous loan at any bank (IDR) | bureau / credit history | candidate feature | integer (stored as decimal) | sum of bureau.prev_amount per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_max_dpd_mean | Mean of: worst days past due on a previous loan | bureau / credit history | in final model | decimal | mean of bureau.prev_max_dpd per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_max_dpd_max | Maximum of: worst days past due on a previous loan | bureau / credit history | candidate feature | integer (stored as decimal) | maximum of bureau.prev_max_dpd per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_max_dpd_min | Minimum of: worst days past due on a previous loan | bureau / credit history | candidate feature | integer (stored as decimal) | minimum of bureau.prev_max_dpd per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| bur_prev_max_dpd_sum | Sum of: worst days past due on a previous loan | bureau / credit history | candidate feature | integer (stored as decimal) | sum of bureau.prev_max_dpd per cust_id; left join bureau on cust_id = cust_id; missing when the key has no bureau record |
| perf_max_dpd_12m | Worst days past due in the 12 months after booking | target / performance | not used | integer | performance.max_dpd_12m; left join performance on app_id = app_id; missing when the key has no performance record |
| bad | Bad flag: the outcome the scorecard predicts | target / performance | target | integer (stored as decimal) | 1 if perf_max_dpd_12m >= 90 (90+ days past due within 12 months), else 0 |

## Statistics

| variable | count | null_count | null_pct | distinct | min | max | outlier_method | outlier_lower | outlier_upper | outlier_count | outlier_treatment |
|---|---|---|---|---|---|---|---|---|---|---|---|
| app_id | 6000 | 0 | 0.0% | 6000 |  |  | not applicable (non-numeric) |  |  |  | none |
| cust_id | 6000 | 0 | 0.0% | 3616 |  |  | not applicable (non-numeric) |  |  |  | none |
| app_date | 6000 | 0 | 0.0% | 730 | 2023-01-01 | 2024-12-30 | not applicable (non-numeric) |  |  |  | none |
| loan_amount | 6000 | 0 | 0.0% | 525 | 1,600,000 | 120,700,000 | iqr | -7,200,000 | 38,400,000 | 282 | none |
| tenor_months | 6000 | 0 | 0.0% | 5 | 6 | 36 | not tested (≤ 10 distinct values) |  |  |  | none |
| channel | 6000 | 0 | 0.0% | 3 |  |  | not applicable (non-numeric) |  |  |  | none |
| is_staff_loan | 6000 | 0 | 0.0% | 2 | 0 | 1 | not tested (≤ 10 distinct values) |  |  |  | none |
| noise_a | 6000 | 0 | 0.0% | 6000 | -3.294 | 3.713 | iqr | -2.694 | 2.737 | 47 | none |
| noise_b | 6000 | 0 | 0.0% | 4 |  |  | not applicable (non-numeric) |  |  |  | none |
| cust_age | 6000 | 0 | 0.0% | 44 | 21 | 64 | iqr | -3.5 | 88.5 | 0 | none |
| cust_income | 6000 | 0 | 0.0% | 1145 | 1,020,000 | 45,390,000 | iqr | -1,840,000 | 14,400,000 | 181 | none |
| cust_income_band | 6000 | 0 | 0.0% | 4 |  |  | not applicable (non-numeric) |  |  |  | none |
| cust_marital_status | 6000 | 0 | 0.0% | 3 |  |  | not applicable (non-numeric) |  |  |  | none |
| cust_province | 6000 | 0 | 0.0% | 7 |  |  | not applicable (non-numeric) |  |  |  | none |
| cust_years_at_job | 6000 | 420 | 7.0% | 235 | 0 | 48.4 | iqr | -7.15 | 15.65 | 225 | none |
| cust_spouse_income | 6000 | 3026 | 50.4% | 833 | 690,000 | 26,280,000 | iqr | -2,812,500 | 12,327,500 | 128 | none |
| bur_count | 6000 | 789 | 13.2% | 8 | 1 | 8 | not tested (≤ 10 distinct values) |  |  |  | none |
| bur_prev_amount_mean | 6000 | 789 | 13.2% | 2077 | 380,000 | 41,870,000 | iqr | -2,160,312 | 14,672,188 | 250 | none |
| bur_prev_amount_max | 6000 | 789 | 13.2% | 1540 | 380,000 | 73,270,000 | iqr | -5,435,000 | 22,685,000 | 251 | none |
| bur_prev_amount_min | 6000 | 789 | 13.2% | 1018 | 340,000 | 41,870,000 | iqr | -2,623,750 | 10,506,250 | 355 | none |
| bur_prev_amount_sum | 6000 | 789 | 13.2% | 2017 | 380,000 | 91,820,000 | iqr | -15,066,250 | 43,243,750 | 199 | none |
| bur_prev_max_dpd_mean | 6000 | 789 | 13.2% | 273 | 0 | 185 | iqr | -8.25 | 17.75 | 420 | none |
| bur_prev_max_dpd_max | 6000 | 789 | 13.2% | 104 | 0 | 234 | iqr | -13 | 27 | 413 | none |
| bur_prev_max_dpd_min | 6000 | 789 | 13.2% | 61 | 0 | 185 | iqr | -6 | 10 | 475 | none |
| bur_prev_max_dpd_sum | 6000 | 789 | 13.2% | 136 | 0 | 484 | iqr | -18 | 38 | 497 | none |
| perf_max_dpd_12m | 6000 | 0 | 0.0% | 151 | 0 | 180 | not tested (not a candidate feature) |  |  |  | none |
| bad | 6000 | 0 | 0.0% | 2 | 0 | 1 | not tested (not a candidate feature) |  |  |  | none |

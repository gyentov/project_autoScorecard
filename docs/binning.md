# Binning, WoE and IV

Code: `autoscorecard/engine/binning.py`. Convention: y = 1 means **bad**.

```
WoE_i = ln(dist_good_i / dist_bad_i)        higher WoE = safer bin
IV    = Σ (dist_good_i − dist_bad_i) × WoE_i
```

A bin with zero goods or zero bads uses 0.5 in place of the zero, so WoE stays
finite. The merge rules below normally stop that from happening anyway.

## Numeric features: monotonic by construction

1. **Fine classing.** Up to 20 quantile bins, or one bin per distinct value when
   there are 20 or fewer. Intervals are right-closed, `(lo, hi]`, and the outer
   edges are −inf and +inf, so every future value has a bin.
2. **Merging.** Repeatedly merge the two adjacent bins with the smallest
   chi-square (the two most similar bins), fixing rules in this priority:
   1. a bin with no goods or no bads,
   2. a bin with fewer than `min_bin_pct` (5%) of non-missing rows,
   3. **bad rate not strictly monotonic**: two neighbours with equal bad rates
      also count as a violation,
   4. more than `max_bins` (8) bins.
3. **Direction.** With `monotonic="auto"` (default) the merge runs twice, once
   for rising and once for falling bad rate, and keeps the direction with the
   higher IV. The choice is the first line of the binning's `merge_log`.
   `"ascending"` / `"descending"` force a direction; `None` turns the rule off.
4. **Missing values** get their own bin. It is not part of the monotonic rule,
   because it has no position on the value axis.

The result: across the non-missing bins, the bad rate (and so the WoE and the
points) moves in one direction only. `VariableBinning.is_monotonic` checks this,
and the waterfall report states it for every selected feature.

**Curved relationships** (for example a U-shape in age) are forced into one
direction, and the weaker arm is merged into a single large bin. That bin's
size, shown in the bin table, is the signal to consider a transformation or
manual bins.

## Categorical features

Categories under `rare_pct` (1%) of rows are pooled into `__OTHER__`. Groups are
sorted by bad rate and merged with the same rules, so WoE is ordered by
construction. Categories not seen in training map to `__OTHER__`, or get WoE 0
when there is no `__OTHER__` bin.

## Manual bins

`ScorecardConfig.manual_bins = {"age": {"edges": [30, 45, 55]}}` (numeric) or
`{"province": {"groups": [["DKI", "Banten"], ["Jabar"], ["__OTHER__"]]}}`.
Manual bins are used exactly as given and are **never merged**. If they are not
monotonic, `is_monotonic` is false, the audit log records it, and the waterfall
report lists the feature under "Binning check".

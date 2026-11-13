# model-promotion Specification

## Purpose

Decides, reproducibly and with an audit trail, which registered model version is the champion that serving uses, and allows reverting that decision.

## Requirements

### Requirement: Absolute quality gate
A candidate version SHALL be eligible for promotion only if its metrics on its held-out test set meet every configured minimum. The minimums SHALL be read from the versioned parameters file (at least `min_roc_auc` and `min_f1`).

#### Scenario: Below threshold
- **WHEN** a candidate has `roc_auc` below `min_roc_auc`
- **THEN** it is not promoted, and the decision names the failed threshold

### Requirement: Champion comparison on identical data
When a champion exists, the candidate and the champion SHALL both be scored on the candidate's held-out test set. The candidate SHALL be promoted only if its `roc_auc` exceeds the champion's by at least the configured `min_improvement`.

#### Scenario: Better candidate
- **WHEN** an eligible candidate scores 0.86 ROC-AUC and the champion 0.82 on the same test set, with `min_improvement` 0.005
- **THEN** the candidate becomes champion

#### Scenario: Not better
- **WHEN** an eligible candidate scores no more than the champion plus `min_improvement`
- **THEN** the champion is unchanged and the decision states that the candidate did not beat it

#### Scenario: First model
- **WHEN** no version carries the `champion` alias and the candidate passes the absolute gate
- **THEN** the candidate becomes champion

### Requirement: Alias-based promotion
Promotion SHALL move the `champion` alias to the candidate and move `previous-champion` to the version that was champion. Deprecated registry stages SHALL NOT be used.

#### Scenario: Alias move
- **WHEN** version 4 is promoted over champion version 3
- **THEN** `champion` resolves to version 4 and `previous-champion` to version 3

### Requirement: Recorded decisions
Every evaluation SHALL tag the candidate version with the decision (`promoted` or `rejected`), the reason, the compared scores and a UTC timestamp. It SHALL also print the decision as a single JSON object on standard output.

#### Scenario: Rejected candidate
- **WHEN** a candidate is rejected
- **THEN** its version tags show `promotion_status=rejected` and the reason, and the JSON output has `"promoted": false`

### Requirement: Dry run
A dry-run mode SHALL compute and print the decision without changing any alias or tag.

#### Scenario: Dry run
- **WHEN** promotion runs with dry-run on an eligible, better candidate
- **THEN** the output says it would be promoted and the registry is unchanged

### Requirement: Rollback
A rollback command SHALL make the `previous-champion` version the `champion` again, and SHALL record the rollback on both versions. It SHALL fail with a clear error when there is no previous champion.

#### Scenario: Revert a promotion
- **WHEN** rollback runs after version 4 replaced version 3
- **THEN** `champion` resolves to version 3 and version 4 is tagged as rolled back

### Requirement: Exit status
The promotion command SHALL exit 0 when it reaches a decision, whether or not it promotes. It SHALL exit non-zero only on errors, such as an unknown model or version, an unreachable registry, or a missing test set, so an orchestrator can tell failures from rejections.

#### Scenario: Unknown version
- **WHEN** promotion is asked to evaluate a version that does not exist
- **THEN** it exits non-zero with an error naming the version

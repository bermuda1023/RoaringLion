# Reference Reconciliation Slot

Reserved for real deals and portfolios with known answers from another system (e.g. AIR Touchstone, RMS RiskModeler, Analyze Re, or internal actuarial spreadsheets).

When real simulation sets and reference deals arrive:
1. Place anonymized/sanitized reference inputs and expected AAL / OEP / AEP / TVaR / recovery outputs here.
2. Add a pytest reconciliation test verifying AAL first, then tail metrics and layer recoveries, before trusting production runs.

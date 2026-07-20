# Customer-master field mapping

| Field group | Canonical owner | Fallback / backfill | Snapshot |
|---|---|---|---|
| Full name, NIC, mobile, email, DOB, civil status | `customers` (`users.email` fallback only) | approved KYC where available; latest submitted/approved application for missing stable values | `loan_applications` values remain immutable |
| Current/permanent structured address, postal code | `customers` | approved KYC, then application during backfill; legacy `customers.address` only becomes `current_address_line1` and requires review | immutable |
| Occupation, employer/business, income/expenses, household/dependents, guarantor | `customers` | approved KYC, then latest submitted/approved application only for fields it contains | immutable |
| Consent, documents, KYC verification/review/risk | `customer_kyc_profiles` / documents | no application fallback | immutable application evidence |
| KYC and eligibility status | `customers` current operational state | approved KYC determines verified fallback eligibility | immutable application decision context |
| Loan terms and loan lifecycle | `loan_applications` / `loans` | never copied to customer master | immutable / lifecycle records |

The approved KYC selector accepts `APPROVED` or `VERIFIED`, sorts by `reviewed_at DESC, id DESC`, and excludes rejected/superseded records. Backfill fills blanks only, reports conflicts, and records application IDs in its preview source. Address display fallback is current structured, permanent structured, then legacy address.

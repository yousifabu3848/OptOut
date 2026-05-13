# Legal Posture and Compliance

This document explains the statutory basis for OptOut's submissions, the obligations brokers have once a request is filed, and the deliberate design choices that keep the tool's legal model clean.

---

## The self-operated model

OptOut is designed to be run by the individual whose data is being removed — not by a third party on that person's behalf.

This is not an accident. Under CCPA §1798.140(ag), a person exercising their own rights is distinct from an "authorized agent" acting for someone else. Authorized agents must satisfy additional verification requirements that vary by broker and are frequently used to slow down or reject requests from commercial services like DeleteMe and Optery.

When you run `optout submit`, you are submitting a request **on your own behalf**, from your own machine, using your own email address. There is no intermediary. Brokers cannot invoke the authorized-agent verification requirements against you.

The AGPL-3.0 license reinforces this: anyone who modifies OptOut and offers it as a hosted service must publish their changes. Running it as a service on behalf of others would reintroduce the authorized-agent problem. The license makes that path unattractive.

---

## Statutes cited in submissions

### CCPA — California Consumer Privacy Act (Cal. Civ. Code §1798.100–§1798.199)

The California Consumer Privacy Act, effective January 1, 2020, gives California residents the right to request deletion of their personal information from businesses that collect it. The key provision for OptOut:

**§1798.105 — Right to Deletion**

> A consumer shall have the right to request that a business delete any personal information about the consumer which the business has collected from the consumer.

**Broker obligations under CCPA:**

- Must respond within **45 calendar days** of a verifiable consumer request (§1798.105(d))
- May extend by an additional 45 days (90 total) with written notice explaining the reason
- Must delete the information **and** direct any service providers to delete it
- Must provide **two or more** designated methods for submitting requests (§1798.130(a)(1))

**Who is covered:** Any for-profit business that does business in California, collects consumers' personal information, and meets one of three thresholds: (1) annual gross revenues over $25M, (2) buys/sells/receives/shares personal info of 100,000+ consumers or households annually, or (3) derives 50%+ of annual revenue from selling personal info. Every major data broker qualifies.

**Verification:** Businesses may ask for information reasonably necessary to verify the request. OptOut provides name, address, and email — sufficient for the consumer-direct opt-out flows each broker maintains.

---

### CPRA — California Privacy Rights Act (Cal. Civ. Code §1798.100–§1798.199, amended)

The CPRA, effective January 1, 2023, strengthens CCPA enforcement and adds several relevant provisions:

- Creates the **California Privacy Protection Agency (CPPA)**, an independent enforcement agency with rulemaking authority
- Adds the right to **correct** inaccurate personal information (§1798.106) — not yet implemented in OptOut
- Extends deletion rights to personal information collected **indirectly** (i.e., data brokers who never interacted with you directly must comply)
- Adds a **"sensitive personal information"** category with stricter handling requirements; this includes precise geolocation, biometrics, financial account details, and health information
- Data brokers are now required to register annually with the CPPA (§1798.99.80)

OptOut cites both CCPA and CPRA in submissions where both apply.

---

### GDPR — General Data Protection Regulation (EU 2016/679)

**Article 17 — Right to Erasure ("Right to Be Forgotten")**

Data subjects have the right to request erasure of personal data when:
- The data is no longer necessary for the purpose it was collected
- Consent is withdrawn and there is no other legal basis
- The data subject objects and there is no overriding legitimate interest
- The data was unlawfully processed

**Controller obligations under GDPR Art. 17:**

- Must respond within **30 calendar days** (Art. 12(3)); extendable by two additional months with notice
- Must erase the data without undue delay
- Must inform any recipients to whom the data was disclosed

OptOut includes GDPR Art. 17 citations in submissions to brokers that operate in or offer services to EU/EEA residents — primarily for US-based brokers whose sites are accessible in Europe. Whether a given broker considers itself a GDPR controller for a given data subject is a separate legal question; the citation establishes the basis for the request.

---

### Other state statutes

The following laws are cited in broker YAMLs where applicable. Coverage depends on the data subject's state of residence.

| Statute | State | Right | Response window |
|---------|-------|-------|----------------|
| VCDPA — Virginia Consumer Data Protection Act | Virginia | Deletion | 45 days (+45 extension) |
| CPA — Colorado Privacy Act | Colorado | Deletion | 45 days (+45 extension) |
| CTDPA — Connecticut Data Privacy Act | Connecticut | Deletion | 45 days (+45 extension) |
| UCPA — Utah Consumer Privacy Act | Utah | Deletion | 45 days (+45 extension) |
| MCDPA — Minnesota Consumer Data Privacy Act | Minnesota | Deletion | 45 days (+45 extension) |
| TDPSA — Texas Data Privacy and Security Act | Texas | Deletion | 45 days (+45 extension) |

Each broker's YAML declares which statutes apply in the `legal_basis` field. OptOut does not auto-select statutes based on the user's state; the YAML author is responsible for listing the applicable laws for the broker's coverage area.

---

## What brokers are required to do

Under CCPA (the most broadly applicable US statute), a covered business that receives a verifiable consumer request must:

1. **Confirm receipt** of the request within 10 business days
2. **Complete the deletion** within 45 calendar days, or notify the consumer of an extension
3. **Not sell or share** the deleted data after deletion (§1798.105(d))
4. **Not discriminate** against the consumer for exercising their rights (§1798.125)
5. **Maintain records** of deletion requests for 24 months (CPRA)

Brokers are not required to delete data that is:
- Necessary to complete a transaction the consumer requested
- Used for internal purposes reasonably aligned with the consumer's expectations
- Required to comply with a legal obligation
- Used for certain security purposes

In practice, data brokers rarely invoke these exceptions for consumer opt-out requests — the data they hold typically has no transactional basis. If a broker rejects a request and cites one of these exceptions, `optout status` records the rejection and the response for follow-up.

---

## Enforcement

**CCPA/CPRA:** Enforced by the California Attorney General and the CPPA. Statutory damages: up to $2,500 per unintentional violation, $7,500 per intentional violation. Private right of action exists only for data breaches (§1798.150), not for failure to delete — meaning consumers must file complaints with the CPPA rather than sue directly.

**GDPR:** Enforced by EU/EEA data protection authorities (DPAs). Fines up to €20M or 4% of global annual turnover (whichever is higher) for serious violations.

**Practical reality:** Most data brokers comply eventually. Some drag out the 45-day window. A small number ignore requests entirely. OptOut tracks deadlines and surfaces overdue submissions in `optout status` so you can decide whether to escalate (file a CPPA complaint, send a follow-up request citing the missed deadline, or document the non-compliance).

---

## What OptOut does not guarantee

- **Removal is not guaranteed.** The tool submits the request. Compliance is the broker's legal obligation, not something the tool can force.
- **Data reappears.** Brokers rebuild their databases from public records. 60–90 days is a typical re-add window. Run `optout monitor` on a schedule to catch this.
- **OptOut does not send legal threats.** Submissions cite the applicable statute, but the tool does not send cease-and-desist letters or initiate formal complaints. If a broker misses its statutory deadline, the next step is a human one: file a complaint with the CPPA (`cppa.ca.gov`) or the relevant state attorney general.
- **Non-California residents.** CCPA applies to California residents. If you do not reside in California, only the state-specific laws that apply to your state of residence are legally enforceable against brokers — though many brokers honor CCPA-style requests from all US residents as a matter of policy.

---

## Disclosure and citation language

Each web form submission fills in the applicable statute citation wherever the form provides a "reason" or "legal basis" field. For email submissions, OptOut's email template includes the following standard paragraph:

> This request is submitted pursuant to the California Consumer Privacy Act (Cal. Civ. Code §1798.105), the California Privacy Rights Act, and any other applicable state privacy law. I am the individual whose data is described above and I am submitting this request on my own behalf. Please confirm receipt within 10 business days and complete deletion within the statutory window.

The exact text is in `src/optout/engine/methods/email.py`.

---

## Adding legal basis to a new broker

In a broker's YAML, set `legal_basis` to a list of applicable statutes:

```yaml
legal_basis: [CCPA, CPRA]           # most US data brokers
# legal_basis: [CCPA, CPRA, GDPR]  # if the broker has EU operations
# legal_basis: [GDPR]               # EU-only brokers
```

Set `statutory_response_days` to the shortest applicable deadline:

```yaml
statutory_response_days: 45   # CCPA / most US state laws
# statutory_response_days: 30  # GDPR
```

OptOut uses `statutory_response_days` to calculate the deadline stored in the database and surfaced in `optout status`.

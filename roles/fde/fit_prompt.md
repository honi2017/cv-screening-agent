# Pass B — Fit

Score this CV against the Forward Deployed Engineer role. The RedFlag pass has
already found the problems — they are given below, and you must not re-derive
them. Score the evidence that is present.

The CV is redacted: `[NAME]`, `[EMAIL]`, `[PHONE]`, `[LOCATION]`, `[SCHOOL]`.
That is expected.

## The role, in one paragraph

A technical solutioning role. The engineer works directly with high-profile
clients — investment firms, law firms, and hedge funds — to scope and deliver
reliable integrations between the client's systems and Anduin's platform;
leads exploratory meetings to extract deep context before proposing a
solution; explores clients' internal systems (CRMs, data models, workflows)
to identify friction points and advise on data-structure improvements;
implements subscription-form digitisation logic; and turns one-off client
work into repeatable platform capabilities that benefit every future client.
It demands 5+ years of software engineering experience with a track record of
owning and shipping production systems independently, the judgment to make
fast, sound decisions under pressure, hands-on breadth across integration
domains (database management systems, SFTP, SMTP, enterprise SSO), proven
client-facing experience, and the communication to hold both an engineering
and a client conversation across time zones. Fully remote and US-based
(East Coast or Midwest is a plus), collaborating with US and Vietnam-based
teams, sometimes in the evening.

## Criteria

Score each independently. The maximum is in brackets.

- **`production_ownership` [25]** — Shipped and owned production systems
  independently. Named systems, scale figures, stack, on-call or incident
  ownership. Evidence of making sound decisions under pressure.
- **`integration_breadth` [20]** — Hands-on across *several* integration
  domains: database management systems, SFTP, SMTP or email pipelines,
  enterprise SSO (SAML, OIDC, Okta, Azure AD), REST and webhook APIs, CRMs
  (Salesforce, HubSpot, DealCloud), data-model design. Breadth matters more
  than depth in any one.
- **`client_solutioning` [20]** — Led discovery or scoping with external
  clients, translated technical concepts for non-engineers, handled
  escalations professionally. A prior FDE, solutions engineering,
  implementation, or consulting role is strong evidence.
- **`communication_product` [15]** — Trade-off reasoning visible in the
  writing. Turned client-specific work into reusable capability. The CV
  itself is a writing sample for a client-facing role: clarity counts,
  verbosity does not.
- **`domain` [10]** — Fintech, private markets, enterprise SaaS, document
  digitisation, OCR, or subscription-form-processing experience.
- **`ai_tooling` [5]** — Built internal automation, agents, or LLM tooling
  with a real outcome. "Familiar with ChatGPT" earns nothing.
- **`distributed_collab` [5]** — Worked across time zones, especially with
  offshore teams in Vietnam or similar.

## Anchors

Apply these as a fraction of each criterion's maximum:

- **0.0** — no evidence at all
- **0.25** — claimed, with no specifics
- **0.5** — one concrete, specific example
- **0.75** — several concrete examples with scale or outcomes
- **1.0** — sustained, owned, quantified, unmistakably senior

Round to the nearest whole point. Do not inflate: 0.5 for one solid example is
the correct score, not a penalty.

## Bonus

Up to **5** points for evidence that is exceptional *for this role
specifically* — an integration platform reused across many clients, a
Palantir-style forward-deployed background, an early employee who scaled
client delivery. Justify it in one sentence or award nothing.

## Rules

1. **Every score above 0 must quote the CV verbatim.** Copy the exact text.
   A score whose quote is not found in the CV is zeroed automatically, so
   quote carefully. Score 0 takes `"quote": null`.
2. Score only what the CV supports. Do not infer skills from job titles, and
   do not credit a technology merely because it appears in a skills list.
3. **Fairness:** ignore and never weigh nationality, gender, age, or the
   prestige of any institution or employer. Score the work, not the logo.
4. Do not re-litigate the RedFlag findings; penalties are applied outside
   this pass.

## RedFlag result

```json
{{REDFLAG_RESULT}}
```

## Prechecks

```json
{{PRECHECKS}}
```

## The CV

```markdown
{{REDACTED_CV}}
```

## Output

Return **only** this JSON object, no prose:

```json
{
  "scores": {
    "production_ownership": {"score": 18, "quote": "exact CV text", "rationale": "one sentence"},
    "integration_breadth":  {"score": 12, "quote": "exact CV text", "rationale": "one sentence"},
    "client_solutioning":   {"score": 15, "quote": "exact CV text", "rationale": "one sentence"},
    "communication_product":{"score": 9,  "quote": "exact CV text", "rationale": "one sentence"},
    "domain":               {"score": 4,  "quote": "exact CV text", "rationale": "one sentence"},
    "ai_tooling":           {"score": 2,  "quote": "exact CV text", "rationale": "one sentence"},
    "distributed_collab":   {"score": 0,  "quote": null, "rationale": "no evidence"}
  },
  "bonus": {"points": 0, "justification": null},
  "summary": "Two or three sentences: what this candidate is strong at, and the main gap."
}
```

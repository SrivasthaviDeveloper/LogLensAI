# LogLensAI presentation outline and presenter notes

**Deck:** six slides, exactly five minutes (300 seconds). The live demo begins after the presentation and uses a separate five-minute script in LogLens_Demo_Runbook.md. All examples are fictional.

## Slide 1 — LogLensAI provisioning incident triage
**Timing:** 00:00–00:40 (40 seconds)  
**Key message:** Turn a provisioning error into a reviewable call path and request/response evidence.  
**Slide content:** LogLensAI; “Trace a provisioning failure across service boundaries”; “Local simulation with fabricated Hosted/Premise flows.”  
**Visual:** Dark blue field, Georgia headline, light-blue accent rule, restrained product palette from ui.py.  
**Speaker notes:**  
“Provisioning incidents often arrive as one status code and a short error string. The hard part is connecting that message to the service that rejected the request and the dependency it called. LogLensAI’s demo gives us a repeatable way to make that path visible. I’ll show what the current build actually does, including where its evidence comes from.”
**Transition:** “First, let’s look at why the boundary matters.”

## Slide 2 — Provisioning failures cross service boundaries
**Timing:** 00:40–01:30 (50 seconds)  
**Key message:** The final error alone may hide which dependency produced it.  
**Slide content:** An order begins in OrderHub; a Hosted or Premise workflow calls IPSM or ESP; the investigator needs the downstream response and matching log evidence.  
**Visual:** One editable flow: OrderHub → downstream service → evidence, with “request”, “response”, and “logs” labels.  
**Speaker notes:**  
“The demo models two order types. Hosted requests use an IPSM IP allocation service. Premise requests use an ESP credential broker. A caller can surface a 409 or 401, while the underlying downstream response gives the specific failure code. The timeout fixture adds another useful distinction: the caller can time out even when the dependency eventually succeeds. These are synthetic records, but they show the kinds of evidence an operator needs to compare.”
**Transition:** “Here are the three behaviors the simulator currently covers.”

## Slide 3 — Three seeded investigation paths
**Timing:** 01:30–02:30 (60 seconds)  
**Key message:** The demo returns fixed, inspectable evidence for three concrete flows.  
**Slide content:**  
- Premise / ESP: INVALID_AUDIENCE, order response 401  
- Hosted / IPSM: IP_POOL_EXHAUSTED, order response 409  
- Hosted / IPSM: caller deadline 504, downstream response 200 at 2438 ms  
Footer: “Deterministic fixtures; substring matching; no LLM.”  
**Visual:** Three horizontal evidence rows with compact status and dependency labels.  
**Speaker notes:**  
“The current matching is deterministic. It ranks three authored flows by how many configured terms appear in the input. The result includes a likely explanation and suggested checks that are also authored in the simulator. That gives us stable examples for a presentation, but it is not arbitrary log understanding or code discovery. I’m making that boundary explicit so the output isn’t mistaken for production AI.”
**Transition:** “The next slide shows how the evidence reaches the browser.”

## Slide 4 — Local architecture and trust boundaries
**Timing:** 02:30–03:25 (55 seconds)  
**Key message:** Streamlit calls FastAPI, which authenticates to the local Jenkins-compatible controller; that controller reads two dummy servers.  
**Slide content:** Streamlit UI → FastAPI /investigate → controller → simulator trace + authenticated JSONL reads → evidence response.  
**Visual:** Editable architecture diagram. Mark HTTP Basic as fictional, read-only demo access. Keep the old AWS app.py prototype separate and unconfigured.  
**Speaker notes:**  
“The browser is a Streamlit page. It posts the incident to FastAPI. FastAPI uses HTTP Basic to call our local Jenkins-compatible controller. That controller resolves the synthetic trace and then authenticates to the two server-log endpoints using a separate read-only demo account. The server files are JSONL fixtures. This controller mirrors a Jenkins job shape so the laptop demo runs without Jenkins installed; it is not a real Jenkins execution.”
**Transition:** “Let’s inspect the exact result from a Premise failure.”

## Slide 5 — Premise authentication failure, end to end
**Timing:** 03:25–04:25 (60 seconds)  
**Key message:** The two response boundaries identify the mismatch behind the final 401.  
**Slide content:** Input “ESP unauthorized 401 invalid audience”; Order ORD-73106; Premise; route premise-site-agent → ESP credential-broker; order 401 DOWNSTREAM_AUTH_REJECTED; ESP 401 INVALID_AUDIENCE, expected premise-provisioning-demo; Jenkins build success, two dummy servers, four entries.  
**Visual:** Editable extract of the verified UI result using the product’s colors and typography. This is slide text, not a screenshot.  
**Speaker notes:**  
“I enter OrderHub and the sanitized error message. The result selects order ORD-73106, a Premise flow. The order API returns DOWNSTREAM_AUTH_REJECTED with status 401. The ESP request shows the wrong audience and the downstream response names the expected premise audience. The job reports two dummy servers and four reference records. The timestamp is carried through as context only; the fixture query does not filter by it.”
**Transition:** “I’ll close with what is verified and what production work remains.”

## Slide 6 — Verified demo, clear next steps
**Timing:** 04:25–05:00 (35 seconds)  
**Key message:** The local demo and current tests work; production integrations and security remain future work.  
**Slide content:** 32 tests passed; three local API health checks passed; two end-to-end fixture flows passed; next: real Jenkins queue/artifact handling, authentication/redaction, event-time/correlation filtering, simulator/AWS tests.  
**Visual:** Strong closing statement on light blue background with three concise validation points and a short next-work list.  
**Speaker notes:**  
“We verified the existing LogLens suite with 32 passing tests, compiled the application and simulator modules, and exercised Hosted and Premise flows through the local controller. We have not deployed to production or run a real Jenkins or AWS integration. The next engineering step is to replace fixture matching with an authenticated, time- and correlation-aware log adapter, then validate the real Jenkins job lifecycle and access controls.”
**Closing:** “The value of this prototype is making the evidence path visible. The next step is proving that path against approved real systems.”

## Timing check

40 + 50 + 60 + 55 + 60 + 35 = **300 seconds (5:00)**.

## Import and editability note

Slide titles, labels, shapes, and architecture diagram are editable PowerPoint objects. The product walkthrough uses exact output values from a verified local API run, laid out as editable text rather than a screenshot because no screenshot file was available in the workspace.

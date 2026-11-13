# vaani.dev - factual notes for the feedback section (fetched 2026-11-14)

Raw material only. The feedback in the report must be the student's own opinion after visiting the site;
these notes come from an automated text fetch of 5 pages (/, /platform, /pricing, /developers, /about).
Several "0" values are probably animated counters captured before they ran - check in a browser.

What it says it is: "The only voice AI harness that you'll ever need" - voice agents for real phone calls
(lead qualification, admissions, customer calls). Developer API: Call API, in-app WebRTC voice, campaigns,
webhooks, bring-your-own carrier (SIP) and model (WebSocket). Bengaluru team.

Things a reader could check / react to:
- Latency claims differ by page: homepage "p90 ~450ms" / "Sub-500ms"; developers FAQ "p50 is 600ms and p90 is
  900-1200ms". (Relevant to this assignment: which percentile, measured where, full turn or first audio?)
- Developer quickstart is concrete: API key -> configure agent -> test number; cURL to
  api.vaanivoice.ai/api/trigger-call/ with X-API-Key. Console links go to app.vaanivoice.ai while nav says
  platform.vaani.dev - same product?
- Pricing is unusually transparent: per-layer cost (telephony, STT, LLM, TTS, platform) and an estimator
  (example 1,000 calls / 3,000 min -> about Rs 5.66/min). Homepage says "Start free"; pricing page shows a
  prepaid wallet and no stated free tier.
- PAYG concurrency: 2 concurrent calls; extra lines priced per 5 lines/month (relevant to "requests in flight").
- Homepage lists 5 stages (Build, Humanise, Connect, Run, Improve); /platform lists 6 (adds Scale).
- No list of supported languages on these pages, though multilingual / dialect switching is a headline claim.

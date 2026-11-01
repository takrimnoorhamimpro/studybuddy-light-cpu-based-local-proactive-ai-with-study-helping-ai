# Study Buddy v10 — Personal Companion additions

## What's included
- **My World** button opens a local interactive Tk canvas map plus a selectable memory list.
- Personal knowledge nodes and relationships are stored in the existing per-user `memory.db`; no second LLM or cloud service is added.
- Memories have `confirmed` or `candidate` status. Confirmed facts require explicit confirmation/consent; candidates are not presented as facts. Users can add, confirm, or forget nodes.
- A persistent follow-up table supports one-shot scheduled check-ins. My World includes a “I'm busy — ask later…” action; due onboarding follow-ups are delivered only when chat is not busy, proactive generation is not running, quiet hours are off, and proactive check-ins are enabled.
- Existing Windows foreground window-title capture and proactive title-context path are preserved. Private/auth/payment/medical titles are redacted; title clues are only used when sufficiently specific. No screenshots, page contents, or keystrokes are collected.
- Existing one-model Ollama architecture remains unchanged.

## Run
Extract the full ZIP into a new folder, run `setup_cpu_optimized.bat`, then `run_study_buddy.bat`. Keep your previous project and per-user database as a backup until you've verified the new build on your machine.

## Important verification boundary
The Python source compiles, and the repository test suite passes in this build environment. This is not a claim of production certification: GUI interaction, actual Windows foreground-title capture, notification delivery while the app is closed, real Ollama generation latency, and long-duration scheduler behavior still require testing on the target Windows PC. The graph is an editable first version using Tk Canvas, not a full force-directed Cytoscape graph. Relationships are stored in the schema but the UI currently focuses on browsing/confirming/forgetting nodes.

# Study Buddy v11 — Mermaid source for Figures 3.1 to 3.7

How to use: paste each block into https://mermaid.live, check it, then export as SVG or PNG (SVG is sharper for Word). Figure numbers and captions match the report. Names come from the real code (class names, table keys, foreign keys). Where the paper uses a short name, the code name is shown too.

Name mapping (paper to code):
- "DesktopApp" = class `StudyBuddyApp` in app/desktop_app.py
- "LLM" = class `OllamaClient` in app/llm.py
- "PromptBuilder" = module app/prompt_builder.py (functions `build_system_prompt`, `build_messages`), not a class
- "Safety" = class `Safety` in app/safety.py

---

## Figure 3.1. Study Buddy architecture with one profile database and separate tool telemetry

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis','nodeSpacing':35,'rankSpacing':50},'themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'14px','lineColor':'#475569'}}}%%
flowchart TB
  STUDENT([Student]):::actor

  subgraph UI["Interface layer (app/desktop_app.py, Tkinter)"]
    direction LR
    WIN["Chat window<br/>Tasks, My World, Settings,<br/>Nudge Log, Patterns"]:::ui
    TICK["Scheduler tick<br/>every 5 s"]:::ui
  end

  subgraph CORE["Conversation layer (ChatCore)"]
    direction LR
    SAFE["Safety<br/>input and output checks"]:::core --> ROUTE["IntentRouter<br/>fast paths, dialogs"]:::core --> TOOLS["ToolOrchestrator<br/>approved tools only"]:::core --> PROMPT["Prompt builder<br/>budget about 1,800 est. tokens"]:::core
  end

  subgraph PRO["Proactive layer"]
    direction LR
    CTX["ContextEngine<br/>app category,<br/>filtered window title"]:::pro --> POLICY["InitiativePolicy<br/>hard gates, score, limits"]:::pro --> FB["FeedbackLogger<br/>RhythmTracker<br/>OpenLoopTracker"]:::pro
  end

  subgraph SVC["Privacy and memory services"]
    direction LR
    CONSENT["PersonalCompanion<br/>candidate memory, consent"]:::svc
    PRIV["PrivacyService<br/>review, delete"]:::svc
    REVIEW["Background memory review<br/>after 90 s idle"]:::svc
    BACKUP["Backup manager<br/>encrypted backup, optional EFS"]:::svc
  end

  subgraph MODELS["Local models (Ollama, CPU)"]
    direction LR
    LLM["llama3.2:3b<br/>main chat model"]:::model
    EMB["nomic-embed-text<br/>scene clustering only"]:::model
    CAMM["Optional: Moondream,<br/>ONNX FER+ expression"]:::model
  end

  subgraph STORE["Local storage"]
    direction LR
    DB[("users/PROFILE/memory.db<br/>one profile database:<br/>study, companion, scene tables")]:::store
    TEL[("logs/intelligence.sqlite3<br/>tool_events only<br/>(tool-monitor telemetry)")]:::tele
    CFG[["config/*.json"]]:::store
  end

  STUDENT <--> WIN
  WIN <--> SAFE
  TICK --> CTX
  POLICY <--> WIN
  PROMPT --> LLM
  LLM --> PROMPT
  TOOLS --> DB
  CORE <--> DB
  CONSENT <--> DB
  PRIV --> DB
  REVIEW --> DB
  REVIEW --> LLM
  FB <--> DB
  BACKUP --> DB
  CAMM -.-> PROMPT
  EMB -.-> FB
  TOOLS -.-> TEL

  classDef actor fill:#fde68a,stroke:#b45309,stroke-width:2px,color:#451a03
  classDef ui fill:#bfdbfe,stroke:#1d4ed8,stroke-width:2px,color:#0f172a
  classDef core fill:#ddd6fe,stroke:#6d28d9,stroke-width:2px,color:#2e1065
  classDef pro fill:#fed7aa,stroke:#c2410c,stroke-width:2px,color:#431407
  classDef svc fill:#fbcfe8,stroke:#be185d,stroke-width:2px,color:#500724
  classDef model fill:#bbf7d0,stroke:#15803d,stroke-width:2px,color:#052e16
  classDef store fill:#e2e8f0,stroke:#334155,stroke-width:2px,color:#0f172a
  classDef tele fill:#f1f5f9,stroke:#64748b,stroke-width:2px,stroke-dasharray:5 3,color:#0f172a
```

Note: the dashed telemetry database is separate and is not a second profile database.

---

## Figure 3.2. Use-case diagram for Study Buddy

Mermaid has no native use-case diagram, so this uses a flowchart with grouped subgraphs. Dashed use cases and the dashed optional-features group are off by default in code.

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'linear','nodeSpacing':20,'rankSpacing':40},'themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'14px'}}}%%
flowchart TB
  S([Student]):::actor

  subgraph SYS["Study Buddy (Windows desktop)"]
    direction TB
    subgraph CORE_UC["Core features (always on)"]
      direction LR
      UC1(["Chat and ask study questions"]):::uc
      UC2(["Manage tasks"]):::uc
      UC3(["Set reminders and timers"]):::uc
    end
    subgraph MEM_UC["Memory and privacy controls"]
      direction LR
      UC4(["Review My World memory"]):::uc
      UC5(["Confirm, reject or defer memory candidates"]):::uc
      UC6(["Delete memory and activity data"]):::uc
    end
    subgraph PROMPT_UC["Proactive prompt controls"]
      direction LR
      UC7(["Pause, mute or stop prompts"]):::uc
      UC8(["View prompt reason and Nudge Log"]):::uc
      UC9(["Give prompt feedback"]):::uc
      UC10(["Set quiet hours and daily limit"]):::uc
    end
    subgraph SEC_UC["Security"]
      UC11(["Create or restore encrypted backup"]):::uc
    end
    subgraph OPT_UC["Optional features (off by default)"]
      direction LR
      UC12(["Enable rhythm tracking"]):::opt
      UC13(["Allow camera and scene cues"]):::opt
      UC14(["Use voice input and output"]):::opt
      UC15(["Encrypt profile folder with EFS"]):::opt
    end
  end

  O([Ollama / local model]):::ext
  Q([SQLite / local storage]):::ext

  S --> CORE_UC
  S --> MEM_UC
  S --> PROMPT_UC
  S --> SEC_UC
  S -.-> OPT_UC

  UC1 --- O
  UC1 --- Q
  UC2 --- Q
  UC3 --- Q
  UC13 -.- O

  classDef actor fill:#fde68a,stroke:#b45309,stroke-width:2px,color:#451a03
  classDef ext fill:#e2e8f0,stroke:#334155,stroke-width:2px,color:#0f172a
  classDef uc fill:#dbeafe,stroke:#1d4ed8,stroke-width:1.5px,color:#0f172a
  classDef opt fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,stroke-dasharray:5 3,color:#0f172a
  style SYS fill:#f8fafc,stroke:#334155,stroke-width:2px
  style CORE_UC fill:#eff6ff,stroke:#93c5fd,stroke-width:1px
  style MEM_UC fill:#fdf4ff,stroke:#d8b4fe,stroke-width:1px
  style PROMPT_UC fill:#fff7ed,stroke:#fdba74,stroke-width:1px
  style SEC_UC fill:#f0fdf4,stroke:#86efac,stroke-width:1px
  style OPT_UC fill:#f8fafc,stroke:#94a3b8,stroke-width:1px,stroke-dasharray:5 3
```

---

## Figure 3.3. Main class and service relationships

Real class names from the code. Only attributes and methods that exist in the source are listed.

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'13px'}}}%%
classDiagram
  direction LR

  class StudyBuddyApp {
    <<app/desktop_app.py>>
    Tkinter UI
    _scheduler_tick()
  }
  class ChatCore {
    handle_message(user_text)
  }
  class OllamaClient {
    <<LLM access>>
    chat(messages)
  }
  class Safety {
    check_input(text)
    check_output(text)
  }
  class IntentRouter
  class ToolOrchestrator
  class ToolRegistry
  class prompt_builder {
    <<module>>
    build_system_prompt()
    build_messages()
  }
  class Memory {
    <<SQLite memory.db>>
  }
  class PersonalCompanion {
    candidates, consent, follow-ups
  }
  class UserModel {
    retrieves confirmed memory
  }
  class RelationshipBuilder
  class InitiativePolicy {
    evaluate()
  }
  class ContextEngine {
    observe()
  }
  class RhythmTracker
  class FeedbackLogger
  class OpenLoopTracker {
    sync()
  }
  class SceneSituationModel {
    observe()
  }
  class PrivacyService {
    delete_rhythm()
    delete_nudge_history()
    delete_all_proactive_data()
  }
  class SceneAnalyzer {
    <<optional camera>>
  }
  class CameraExpressionAnalyzer {
    <<optional camera>>
  }
  class CameraPolicy {
    can_start()
  }
  class ToolMonitor {
    <<logs/intelligence.sqlite3>>
    start()
    finish()
  }

  StudyBuddyApp *-- ChatCore
  StudyBuddyApp *-- ContextEngine
  StudyBuddyApp *-- CameraPolicy
  StudyBuddyApp *-- ToolMonitor
  StudyBuddyApp o-- SceneAnalyzer
  StudyBuddyApp o-- CameraExpressionAnalyzer
  StudyBuddyApp ..> PrivacyService : uses
  StudyBuddyApp ..> ToolOrchestrator : injects

  ChatCore o-- OllamaClient
  ChatCore o-- Safety
  ChatCore o-- IntentRouter
  ChatCore o-- ToolOrchestrator
  ChatCore o-- Memory
  ChatCore o-- PersonalCompanion
  ChatCore o-- UserModel
  ChatCore o-- RelationshipBuilder
  ChatCore o-- InitiativePolicy
  ChatCore o-- RhythmTracker
  ChatCore o-- FeedbackLogger
  ChatCore o-- OpenLoopTracker
  ChatCore o-- SceneSituationModel
  ChatCore ..> prompt_builder : builds prompts

  ToolOrchestrator --> ToolRegistry
  PersonalCompanion --> Memory
  UserModel --> Memory
  UserModel --> PersonalCompanion
  InitiativePolicy --> Memory
  InitiativePolicy ..> FeedbackLogger
  InitiativePolicy ..> RhythmTracker
  InitiativePolicy ..> OpenLoopTracker
  InitiativePolicy ..> SceneSituationModel
  PrivacyService --> Memory
  SceneAnalyzer ..> SceneSituationModel : caption text
```

---

## Figure 3.4. Sequence diagram for a normal chat turn

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'14px','actorBkg':'#ddd6fe','actorBorder':'#6d28d9','actorTextColor':'#2e1065','noteBkgColor':'#fef3c7','noteBorderColor':'#b45309'}}}%%
sequenceDiagram
  autonumber
  actor S as Student
  participant UI as StudyBuddyApp (UI)
  participant CC as ChatCore
  participant SF as Safety
  participant IR as IntentRouter
  participant TO as ToolOrchestrator
  participant PB as Prompt builder
  participant LL as Ollama llama3.2:3b
  participant DB as memory.db
  participant CN as PersonalCompanion (consent)

  S->>UI: types a message
  UI->>CC: handle_message(text)
  CC->>SF: check_input
  alt high-risk input
    SF-->>UI: fixed safety reply
    UI-->>S: shows safety reply
  else normal input
    CC->>IR: pending dialog or fast path?
    alt handled by code (task, reminder, timer, consent answer)
      IR-->>CC: result without model call
      CC->>DB: run approved handler, save turn
    else needs the model
      CC->>TO: select approved tools (BM25)
      opt tool needed
        TO->>LL: request with tool schemas
        LL-->>TO: tool call
        TO->>DB: run allow-listed handler
      end
      CC->>PB: confirmed memory, recent turns, safety core
      PB->>LL: prompt within budget
      LL-->>CC: streamed reply
    end
    CC->>SF: check_output
    CC->>DB: save turn
    CC->>CN: rule-based extraction (no model call)
    CN->>DB: candidate memory only
    Note over CN,DB: Candidate is never used in prompts until the student confirms it
    CC-->>UI: reply
    UI-->>S: shows reply
  end
```

---

## Figure 3.5. Proactive initiative and optional camera processing flow

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis','nodeSpacing':30,'rankSpacing':45},'themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'14px','lineColor':'#475569'}}}%%
flowchart TB
  subgraph A["A. Proactive decision (every 5 s tick)"]
    direction TB
    T["Scheduler tick"]:::step --> C["ContextEngine summary<br/>app category, filtered title<br/>and UI state"]:::step
    C --> TR["Possible trigger<br/>deadline, study start, session return,<br/>wind-down, title clue, scene cue"]:::step
    TR --> G{"Hard gates pass?<br/>consent, quiet hours, pause,<br/>typing, open dialog,<br/>private or meeting window,<br/>cooldowns, daily cap"}:::gate
    G -- no --> N1["No prompt"]:::bad
    G -- yes --> SC["Score<br/>base value x acceptance x timing<br/>minus annoyance cost"]:::step
    SC --> TH{"Above threshold?"}:::gate
    TH -- no --> N2["No prompt"]:::bad
    TH -- yes --> CHK{"Title prompt and<br/>window changed?"}:::gate
    CHK -- yes --> N3["Discard prompt"]:::bad
    CHK -- no --> P["Show prompt with visible reason<br/>accept, later, skip, mute"]:::ui
    P --> FBK["Student reaction<br/>replied, ignored, negative,<br/>muted or unseen"]:::ui
    FBK --> UPD["FeedbackLogger updates<br/>acceptance by trigger and 3-hour bucket"]:::learn
  end

  subgraph B["B. Optional local camera (off unless enabled and permitted)"]
    direction TB
    CP{"webcam_enabled<br/>and permission?"}:::gate
    CP -- no --> OFF["Camera not started<br/>chat still works"]:::bad
    CP -- yes --> FR["Frame processed in memory<br/>not intended to be saved"]:::cam
    FR --> SA["Moondream caption<br/>(scene description)"]:::cam
    FR --> EX["ONNX FER+ expression cue<br/>(experimental)"]:::cam
    SA --> CLN{"Caption clean?"}:::gate
    CLN -- no --> DISC["Discard"]:::bad
    CLN -- yes --> SM["SceneSituationModel<br/>cluster and dwell"]:::cam
    SM --> CUE["Scene cue<br/>scene_change or scene_dwell"]:::cam
  end

  CUE -. candidate trigger .-> TR
  EX -. text context only .-> C

  classDef step fill:#fed7aa,stroke:#c2410c,stroke-width:2px,color:#431407
  classDef gate fill:#fef08a,stroke:#a16207,stroke-width:2px,color:#422006
  classDef bad fill:#fecaca,stroke:#b91c1c,stroke-width:2px,color:#450a0a
  classDef ui fill:#bfdbfe,stroke:#1d4ed8,stroke-width:2px,color:#0f172a
  classDef learn fill:#99f6e4,stroke:#0f766e,stroke-width:2px,color:#042f2e
  classDef cam fill:#bbf7d0,stroke:#15803d,stroke-width:2px,color:#052e16
  style A fill:#fff7ed,stroke:#c2410c,stroke-width:2px
  style B fill:#f0fdf4,stroke:#15803d,stroke-width:2px
```

---

## Figure 3.6. Entity-relationship diagram for one profile database and separate tool-monitor telemetry

Solid lines are declared foreign keys (3 in the schema). Dotted lines are logical references with no FK constraint in the schema. `mood_daily` and `growth_log` are Phase 3 stubs, outside the evaluated scope. Composite keys are marked PK on each column of the key. If the diagram is too wide, split it into parts: (a) study tables, (b) companion tables, (c) scene tables.

```mermaid
erDiagram
  %% ===== users/PROFILE/memory.db : base study tables =====
  turns {
    int id PK
    real ts
    text role
    text text
  }
  facts {
    int id PK
    real ts
    text text "unique"
    text source
  }
  trusted_people {
    int id PK
    text name "unique"
  }
  profile {
    text key PK
    text value
  }
  notes {
    int id PK
    real ts
    text level
    text period
    text text
  }
  notes_fts {
    text text "FTS virtual index"
  }
  tasks {
    int id PK
    text title
    text details
    real due_ts
    text status
    real created_ts
    real completed_ts
  }
  reminders {
    int id PK
    text text
    real remind_ts
    int fired
    real created_ts
  }
  personality_versions {
    int id PK
    real created_ts
    text status
    text reason
    text character_json
    text evaluation_json
  }

  %% ===== companion tables =====
  personal_nodes {
    text id PK
    text label
    text kind
    text status
    int consented
    text evidence
    real importance
    text sensitivity
    text source
    real expires_ts
  }
  personal_edges {
    text id PK
    text source_id FK
    text target_id FK
    text relation
    text evidence
    real created_ts
  }
  candidate_queue {
    text node_id PK, FK
    real queued_ts
    int deferrals
    real last_prompted_ts
  }
  consent_ledger {
    int id PK
    real ts
    text scope
    int granted
    text note
  }
  companion_followups {
    text id PK
    real due_ts
    text kind
    text payload
    text status
  }
  companion_settings {
    text key PK
    text value
  }
  companion_events {
    int id PK
    real ts
    text kind
    text trigger
    text reason
    text meta
    text nudge_id
  }
  open_loops {
    text id PK
    text title
    text kind
    real due_ts
    text status
    int step
    text node_id "logical ref"
  }
  rhythm_buckets {
    int dow PK
    int hour PK
    text app_category PK
    real minutes
    int samples
    real updated_ts
  }
  rhythm_days {
    text day PK
    real updated_ts
  }
  trigger_stats {
    text trigger PK
    int hour_bucket PK
    real alpha
    real beta
    real updated_ts
  }
  mood_daily {
    text day PK
    real valence
    int n
  }
  growth_log {
    int id PK
    real ts
    text field
    text old
    text new
    text reason
    int reverted
  }

  %% ===== scene tables =====
  scene_obs {
    int id PK
    real ts
    text text
  }
  scene_clusters {
    int id PK
    text centroid
    int n
    real first_ts
    real last_ts
  }
  scene_visits {
    int id PK
    int cluster_id "logical ref"
    real start_ts
    real dwell_s
    int hour_bucket
    text day
  }
  scene_arms {
    int cluster_id PK "logical ref"
    int hour_bucket PK
    real alpha
    real beta
    real updated_ts
  }
  scene_arm_done {
    text nudge_id PK
  }
  scene_terms {
    int bucket PK
    int df
  }
  scene_meta {
    text key PK
    text value
  }

  %% ===== separate telemetry database: logs/intelligence.sqlite3 =====
  tool_events {
    int id PK
    text tool_id
    text tool_name
    text source
    text status
    real started_at
    real finished_at
    int duration_ms
    text summary
    text error
  }

  %% declared foreign keys (solid)
  personal_nodes ||--o{ personal_edges : "source_id"
  personal_nodes ||--o{ personal_edges : "target_id"
  personal_nodes ||--o| candidate_queue : "node_id"

  %% logical references, no FK constraint (dotted)
  personal_nodes ||..o{ open_loops : "node_id"
  scene_clusters ||..o{ scene_visits : "cluster_id"
  scene_clusters ||..o{ scene_arms : "cluster_id"
  notes ||..|| notes_fts : "text index"
```

Drawing note: put all tables above in one box labelled `users/<profile>/memory.db`. Draw `tool_events` in a separate small box labelled `logs/intelligence.sqlite3 (tool-monitor telemetry)` with no links to the profile box.

---

## Figure 3.7. User-interface storyboard and navigation flow

Use this as the navigation flow. Put your real screenshots into the six panels (Figure 3.7 in the report). Do not invent screens: take them from the working build.

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'basis','nodeSpacing':35,'rankSpacing':50},'themeVariables':{'fontFamily':'Segoe UI, Arial','fontSize':'14px','lineColor':'#475569'}}}%%
flowchart LR
  P1["Panel 1<br/>Main chat window<br/>first launch and consent entry"]:::panel
  P2["Panel 2<br/>Study question<br/>streamed answer and tool result"]:::panel
  P3["Panel 3<br/>Tasks and reminders<br/>add, edit, complete, due dates"]:::panel
  P4["Panel 4<br/>My World<br/>candidate memory:<br/>confirm, reject, defer"]:::panel
  P5["Panel 5<br/>Proactive prompt<br/>visible reason:<br/>accept, later, skip, mute"]:::panel
  P6["Panel 6<br/>Settings<br/>model status, quiet hours,<br/>privacy, rhythm and camera opt-in"]:::panel

  P1 --> P2 --> P3 --> P4 --> P5 --> P6

  subgraph NAV["Navigation labels"]
    direction LR
    N1["Chat"]:::nav
    N2["Tasks"]:::nav
    N3["My World"]:::nav
    N4["Nudge Log"]:::nav
    N5["Patterns"]:::nav
    N6["Settings"]:::nav
    N7["Camera (optional)"]:::opt
    N8["Pause"]:::nav
    N9["Privacy"]:::nav
  end

  P1 -.- N1
  P3 -.- N2
  P4 -.- N3
  P5 -.- N4
  P6 -.- N6
  P6 -.- N5
  P6 -.- N9
  P5 -.- N8
  P6 -.- N7

  classDef panel fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#0f172a
  classDef nav fill:#e2e8f0,stroke:#334155,stroke-width:1.5px,color:#0f172a
  classDef opt fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,stroke-dasharray:5 3,color:#0f172a
  style NAV fill:#ffffff,stroke:#334155,stroke-width:1.5px
```

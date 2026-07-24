# Relay Arca ubiquitous language

These terms describe the repository work process, not Relay runtime behavior. Product vocabulary remains in `.arca/current/ubi-lang.md`.

| Term | Meaning |
| :--- | :--- |
| **Arca** | Relay's repository-local system of working rules, product bundles, records, templates, and linked knowledge. |
| **working authority** | The document that governs contributor actions; for Arca, `.arca/index.md`. |
| **product bundle** | The five files `index.md`, `ubi-lang.md`, `spec.md`, `design.md`, and `test-list.md` that describe one delivered or targeted product state. |
| **current bundle** | The delivered product bundle in `.arca/current/`; it remains authoritative while a goal is active. |
| **goal bundle** | The five-file target copied from current at P1 and held stable until promotion. |
| **cycle** | One planning-and-building run from issue folding through a zero-gap check and possible promotion. |
| **issue** | An incoming request recorded in a folder with exactly five direct files before it is folded into a goal. |
| **issue archive** | `.arca/issue/archive/`, where completed issue folders retain their exact five-file shape. |
| **residual** | One evidence-backed record for one goal requirement, classified `missing`, `partial`, or `satisfied`. |
| **ticket** | A small, self-contained implementation unit cut from a missing or partial residual. |
| **frozen goal** | The active goal after P2 records its revision; it is not edited during ticket implementation. |
| **P1–P5** | The ordered loop: fold issues, find residuals, cut tickets, write tests, implement and prove. |
| **exact bundle shape** | A required file set with no extra direct files; current and goal have five product files, and issues have five issue files. |
| **artifact route** | A canonical path in `.arca/index.md` for a particular kind of knowledge or work record. |
| **research study** | A bounded, advisory evidence set under `.arca/research/<research-title>/`; it has an index, one result per researcher, and designer next steps, and it cannot override process or product authority. |
| **root wrapper** | Root `AGENTS.md` or `CLAUDE.md`, an entry point that links to `.arca/index.md` and carries no independent process, routing, lifecycle, or product rules. |
| **wiki guidance** | An explanatory Markdown page under `.arca/space/wiki/`; it may connect process, product, and source references but cannot override `.arca/index.md` or delivered product authority. |
| **wiki authority** | The two allowlisted pages in `.arca/space/wiki/`, authoritative only for their explanatory routing and links; they cannot override `.arca/index.md` or delivered product authority. |
| **rule precedence** | The authority ordering defined by `.arca/index.md`: this index governs process/routing/lifecycle, `.arca/current/` governs delivered product behavior, and wiki guidance explains without overriding either. |
| **promotion** | The final byte-for-byte replacement of the current five product files by the goal five-file bundle, followed by goal removal. |
| **idle** | The state in which current is delivered authority and no goal is active. |
| **waiting** | A state where one dependent slice needs an unanswered question; independent work continues. |
| **append-only log** | `.arca/current/log.md`, whose existing lines are never edited; transitions and decisions are added at the end. |
| **safe assumption** | A reversible choice made from repository evidence and recorded in the log when no explicit decision is available. |
| **ask ladder** | The order work-it-out, safe assumption, then one batched question for genuinely unresolved choices. |
| **zero-gap check** | The final P2 comparison proving every goal requirement is satisfied before promotion. |

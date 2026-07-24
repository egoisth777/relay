# Relay Arca index

This file is the sole canonical authority for Relay's Arca agent process, routing, lifecycle, and rule precedence. Start here for any documentation, issue, planning, implementation, or verification request. Root `AGENTS.md` and `CLAUDE.md` are entry-point wrappers only: they link here and must not duplicate or override this file. Product behavior is routed through the delivered bundle; contributor work is routed through the records below.

## Artifact routes

| Need | Canonical route | Shape and authority |
| :--- | :--- | :--- |
| Working rules | `.arca/index.md` | This file; contributor routing and lifecycle authority. |
| Process vocabulary | `.arca/ubi-lang.md` | Definitions for every Arca process term introduced here. |
| Delivered product | `.arca/current/` | Exactly five product files: `index.md`, `ubi-lang.md`, `spec.md`, `design.md`, `test-list.md`; plus `current.md` and `log.md`. |
| Active target | `.arca/goal/` | Absent while idle; when active, exactly the same five product files. It never outranks `current` until promotion. |
| Gaps | `.arca/residual/` | Zero or more Markdown records named `r-relay-<nn>-<condensed-name>.md`, one per target requirement; no fake records when no goal is active. |
| Work units | `.arca/ticket/` | Active Markdown records named `t-<nn>-<condensed-name>.md` from the ticket template; completed records move to `.arca/ticket/archive/`. |
| Incoming work | `.arca/issue/<issue-id>/` | Each active issue folder named `i-<nnn>-<condensed-name>` has exactly five direct files: `index.md`, `ubi-lang.md`, `spec.md`, `design.md`, `test-plan.md`. |
| Issue history | `.arca/issue/archive/<issue-id>/` | Archived issue folders retain the exact five-file shape. |
| Reusable forms | `.arca/tpl/` | `current.md`, `log.md`, `residual.md`, `ticket.md`, and the five issue templates. |
| Research studies | `.arca/research/<research-title>/` | Advisory evidence for one bounded question: `index.md`, one Markdown result per researcher, and `next-steps.md`; it cannot override process or product authority. |
| Product facts | `.arca/space/relay-sp/what/` | Existing Relay architecture, manifest, and flow authorities; link to these rather than duplicating them. |
| Explanatory guidance | `.arca/space/wiki/` | Canonical wiki pages are explanatory Relay guidance; they link to product facts and cannot override current product authority or this index's working rules. Only explicitly allowed canonical pages are visible. |

The `.arca/space/relay-sp/what/` documents remain product knowledge. Wiki pages explain how Relay guidance connects to those authorities and to source integration points; they do not copy product prose, change shipped behavior, or supersede `.arca/current/` or this index.
Runtime data, scheduler/store content, and private scratch material are not Arca artifacts.

## Two rule sets and precedence

**This index wins for process:** `.arca/index.md` is the sole authority for agent process, routing, lifecycle, and precedence. The root wrappers have no independent rules. `.arca/ubi-lang.md` defines process terms used by this authority.

**Product rules** live in `.arca/current/` and describe what the shipped Relay system does. Within the five-file product bundle, `spec.md` defines required behavior, `design.md` defines a conforming implementation, and `test-list.md` defines proof. If `.arca/goal/` exists, its five-file bundle describes the active target only; it becomes delivered authority only through promotion.

**Explanatory guidance** lives in `.arca/space/wiki/` and may link to `.arca/space/relay-sp/what/`, source snippets, and verification commands. Wiki pages cannot override current product authority or this index's working rules. When guidance appears to conflict with either authority, follow this index for process and `.arca/current/` for delivered product behavior, then repair the wiki link or wording.

When rules appear to conflict, apply the owning authority above, record one concise decision in `current/log.md`, and continue.

## P1–P5 loop

1. **P1 — fold issues:** when idle, copy the five delivered product files to a new `.arca/goal/`; retain `.arca/current/`. For each pending issue, record each requirement's disposition as accepted, rejected, duplicate, or deferred; then close the containing issue as integrated or rejected, and link accepted requirements into the goal.
2. **P2 — find residuals:** freeze the goal revision, compare every requirement with the implementation, and write exactly one `missing`, `partial`, or `satisfied` residual record per requirement. No evidence means never `satisfied`.
3. **P3 — cut tickets:** turn each missing or partial residual into one small, self-contained ticket with behavior, design, dependency, and planned-test references. Tickets are approved when created.
4. **P4 — write tests:** make every planned check executable, then challenge the test for wrong answers and boundary cases before implementation.
5. **P5 — implement and prove:** implement one ticket, run the full applicable suite, fix failures, and review. After the last ticket, repeat P2; do not promote until the residual set is zero-gap.

Enter at the nearest step for any request, but catch up earlier finish lines yourself. A request to implement or run QA still requires the applicable planning and test steps. A problem discovered in a frozen goal becomes a new issue; do not edit the frozen goal mid-build.

## State and promotion

`current/current.md` records `phase`, `status`, revisions, active references, and `waiting_on`. `status` is `running`, `waiting`, or `idle`; there is no `blocked` state. `current/log.md` is append-only: add one line for each transition, assumption, rule decision, or repair. Bootstrap is idle with no goal, residual, ticket, or issue record.

Promotion is a clean replacement: after the final zero-gap check, copy the goal's five product files byte-for-byte over the five current product files, verify the replacement, remove the goal bundle, preserve `current.md` and `log.md`, repair relative links, and append the promotion line. Only then return to idle. Never edit product files during the replacement.

## Safe-assumption and ask ladder

1. Work it out from these rules, the existing Relay authorities, and repository conventions.
2. Choose the safest reversible assumption, log `assumed: <choice> — <reason>` in `current/log.md`, and continue independent work.
3. Ask one batched question only for decisions that cannot be derived. Put `waiting_on` in `current/current.md` for the dependent slice only; unrelated work continues.

## Do and don't

**Do**

- Keep `.arca/current/` as the delivered authority while a goal is active.
- Preserve the Relay architecture, manifest, and flow documents as linked product facts.
- Give requirements, residuals, tickets, and tests stable cross-references.
- Keep issue folders exactly five files and archives structurally identical.
- Treat tests and observable evidence as the completion proof; fix failed checks rather than hiding them.

**Don't**

- Do not create a goal, residual, ticket, issue, or placeholder record merely to make a directory exist; absent goal means idle.
- Do not mark a residual satisfied without concrete evidence or edit a frozen goal during P4/P5.
- Do not treat a goal as delivered, or modify `current.md`/`log.md` as product behavior.
- Do not duplicate the `.arca/space/relay-sp/what/` authorities or invent Relay runtime facts.
- Do not use Arca files as runtime storage, mutate `~/.relay/convs/` manually, or touch private/external scheduler and store paths.

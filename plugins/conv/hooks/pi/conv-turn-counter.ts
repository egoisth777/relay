import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * conversate auto-save turn counter (pi / oh-my-pi extension).
 *
 * Counts user prompts within a session and, on every Nth prompt, injects a
 * reminder to persist conversation state via the conv plugin. `content`
 * is what reaches the LLM; `display` surfaces it in the TUI as well.
 *
 * API reference (verified against pi-mono docs/extensions.md):
 *   - default-export factory receives `ExtensionAPI`
 *   - `before_agent_start` fires exactly once per user prompt and may return
 *     `{ message: { customType, content, display } }` to inject context.
 *   - `session_start` fires on startup / new / resume / fork.
 *
 * Installed hook locations:
 *   - pi:  ~/.pi/agent/extensions/conv-turn-counter.ts
 *   - omp: <plugin-root>/.omp/hooks/pre/conv-turn-counter.ts
 */

// Inject the reminder on this cadence: turn 10, 20, 30, ...
const TURN_THRESHOLD = 10;

const REMINDER =
  "CONV AUTO-SAVE: threshold reached - run conv:save via the " +
  "conv plugin, then continue.";

export default function (pi: ExtensionAPI) {
  // Per-session count of user prompts. The extension instance lives for the
  // process, so reset on session_start to track the active conversation
  // (new / resume / fork) rather than the process lifetime.
  let userTurns = 0;

  pi.on("session_start", async () => {
    userTurns = 0;
  });

  pi.on("before_agent_start", async () => {
    userTurns += 1;
    if (userTurns % TURN_THRESHOLD !== 0) {
      return; // nothing to inject this turn
    }
    return {
      message: {
        customType: "conversate-autosave",
        content: REMINDER,
        display: true,
      },
    };
  });
}

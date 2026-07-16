import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * Relay handoff reminder (pi / oh-my-pi extension).
 *
 * Counts user prompts within a session and periodically injects a reminder to
 * capture the current runtime artifact as a Relay handoff.
 *
 * Installed hook locations:
 *   - pi:  ~/.pi/agent/extensions/relay-turn-counter.ts
 *   - omp: <relay-root>/.omp/hooks/pre/relay-turn-counter.ts
 */

const TURN_THRESHOLD = 10;

const REMINDER =
  "RELAY HANDOFF: threshold reached - run /relay:save via the " +
  "Relay plugin, then continue.";

export default function (pi: ExtensionAPI) {
  let userTurns = 0;

  pi.on("session_start", async () => {
    userTurns = 0;
  });

  pi.on("before_agent_start", async () => {
    userTurns += 1;
    if (userTurns % TURN_THRESHOLD !== 0) {
      return;
    }
    return {
      message: {
        customType: "relay-handoff",
        content: REMINDER,
        display: true,
      },
    };
  });
}

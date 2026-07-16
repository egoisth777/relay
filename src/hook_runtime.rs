use serde_json::Value;
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

const MAX_INPUT_BYTES: usize = 1024 * 1024;
const REMINDER: &str =
    "RELAY HANDOFF: threshold reached - run /relay:save via the Relay plugin, then continue.";

/// Process a Codex or Claude prompt-submit hook invocation.
///
pub fn run(args: &[String]) {
    let Some(agent) = parse_agent(args) else {
        return;
    };

    let mut input = Vec::new();
    let read_result = io::stdin()
        .take((MAX_INPUT_BYTES + 1) as u64)
        .read_to_end(&mut input);
    if read_result.is_err() {
        return;
    }

    let Some(state_dir) = hook_state_dir() else {
        return;
    };
    let store = CounterStore::new(state_dir);
    if let Some(reminder) = process_input(&input, agent, &store) {
        let _ = writeln!(io::stdout(), "{reminder}");
    }
}

fn parse_agent(args: &[String]) -> Option<&str> {
    for (index, arg) in args.iter().enumerate() {
        if arg == "--agent" {
            let agent = args.get(index + 1)?.as_str();
            return match agent {
                "codex" | "claude" => Some(agent),
                _ => None,
            };
        }
    }
    Some("codex")
}

fn process_input(input: &[u8], agent: &str, store: &CounterStore) -> Option<String> {
    if input.len() > MAX_INPUT_BYTES || !matches!(agent, "codex" | "claude") {
        return None;
    }
    let value = serde_json::from_slice::<Value>(input).ok()?;
    let event = value_string(
        value
            .get("event")
            .or_else(|| value.get("type"))
            .or_else(|| value.get("hook_event_name")),
    );
    if event != "UserPromptSubmit" {
        return None;
    }

    let mut session = value_string(
        value
            .get("session_id")
            .or_else(|| value.get("sessionId"))
            .or_else(|| value.get("session")),
    );
    if session.trim().is_empty() && agent == "codex" {
        session = value_string(value.get("cwd"));
    }
    if session.trim().is_empty() {
        return None;
    }

    let count = store.increment(&session)?;
    (count % 10 == 0).then(|| REMINDER.to_owned())
}

fn value_string(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(value) => value.to_string(),
        None => String::new(),
    }
}

fn hook_state_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    let root = env::var_os("USERPROFILE");
    #[cfg(not(windows))]
    let root = env::var_os("HOME");
    let root = root.filter(|path| !path.is_empty())?;
    hook_state_dir_from_root(Path::new(&root))
}

fn hook_state_dir_from_root(root: &Path) -> Option<PathBuf> {
    if root.as_os_str().is_empty() || !root.is_absolute() {
        return None;
    }
    let canonical_root = fs::canonicalize(root).ok()?;
    let state_dir = canonical_root
        .join(".relay")
        .join(".semble")
        .join("hook-state");
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&state_dir).ok()?;
    let canonical_state_dir = fs::canonicalize(&state_dir).ok()?;
    if !canonical_state_dir.starts_with(&canonical_root) {
        return None;
    }
    let metadata = fs::metadata(&canonical_state_dir).ok()?;
    if !metadata.is_dir() {
        return None;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        fs::set_permissions(&canonical_state_dir, fs::Permissions::from_mode(0o700)).ok()?;
        let mode = fs::metadata(&canonical_state_dir)
            .ok()?
            .permissions()
            .mode();
        if mode & 0o077 != 0 {
            return None;
        }
    }

    Some(canonical_state_dir)
}

struct HookLock(File);

impl Drop for HookLock {
    fn drop(&mut self) {
        let _ = self.0.unlock();
    }
}

fn try_lock_exclusive(path: &Path) -> io::Result<HookLock> {
    const MAX_ATTEMPTS: usize = 32;
    const RETRY_DELAY: Duration = Duration::from_millis(5);

    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    for attempt in 0..MAX_ATTEMPTS {
        match file.try_lock() {
            Ok(()) => return Ok(HookLock(file)),
            Err(std::fs::TryLockError::WouldBlock) if attempt + 1 < MAX_ATTEMPTS => {
                thread::sleep(RETRY_DELAY);
            }
            Err(std::fs::TryLockError::WouldBlock) => {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "hook lock remained held",
                ));
            }
            Err(std::fs::TryLockError::Error(error)) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::WouldBlock,
        "hook lock remained held",
    ))
}

struct CounterStore {
    state_dir: PathBuf,
}

impl CounterStore {
    fn new(state_dir: PathBuf) -> Self {
        Self { state_dir }
    }

    fn increment(&self, session: &str) -> Option<u64> {
        let hash = session_hash(session);
        let counter = self.state_dir.join(format!("relay-hook-{hash}.count"));
        let lock = self.state_dir.join(format!("relay-hook-{hash}.lock"));
        let _guard = try_lock_exclusive(&lock).ok()?;
        let current = fs::read_to_string(&counter)
            .ok()
            .and_then(|value| value.trim().parse::<u64>().ok())
            .unwrap_or(0);
        let next = current.checked_add(1)?;
        let contents = next.to_string();
        crate::atomic_io::write_atomic(&counter, contents.as_bytes()).ok()?;
        Some(next)
    }
}

fn session_hash(session: &str) -> u64 {
    session.bytes().fold(0u64, |hash, byte| {
        hash.wrapping_mul(131).wrapping_add(byte as u64)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Barrier};
    use std::time::{SystemTime, UNIX_EPOCH};

    struct TestTempDir {
        root: PathBuf,
        path: PathBuf,
    }

    impl TestTempDir {
        fn new() -> Self {
            let suffix = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = env::temp_dir().join(format!("relay-hook-test-{suffix}"));
            fs::create_dir(&root).unwrap();
            let path = hook_state_dir_from_root(&root).unwrap();
            Self { root, path }
        }
    }

    impl Drop for TestTempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn input(event: &str, session: &str) -> Vec<u8> {
        format!(r#"{{"event":"{event}","session_id":"{session}"}}"#).into_bytes()
    }

    #[test]
    fn state_dir_creation_failure_is_silent() {
        let temp = TestTempDir::new();
        let root_file = temp.root.join("not-a-directory");
        fs::write(&root_file, b"").unwrap();
        assert_eq!(hook_state_dir_from_root(&root_file), None);
    }

    #[cfg(unix)]
    #[test]
    fn state_dir_symlink_escape_is_silent() {
        use std::os::unix::fs::symlink;

        let temp = TestTempDir::new();
        fs::remove_dir_all(&temp.path).unwrap();
        let outside = temp.root.with_extension("outside");
        fs::create_dir(&outside).unwrap();
        let link = temp.root.join(".relay").join(".semble").join("hook-state");
        symlink(&outside, &link).unwrap();
        assert_eq!(hook_state_dir_from_root(&temp.root), None);
        fs::remove_dir_all(outside).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn state_dir_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let temp = TestTempDir::new();
        let mode = fs::metadata(&temp.path).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0);
    }

    #[test]
    fn held_lock_fails_within_retry_bound() {
        let temp = TestTempDir::new();
        let lock_path = temp
            .path
            .join(format!("relay-hook-{}.lock", session_hash("held")));
        let barrier = Arc::new(Barrier::new(2));
        let holder_barrier = Arc::clone(&barrier);
        let holder_path = lock_path.clone();
        let holder = std::thread::spawn(move || {
            let guard = try_lock_exclusive(&holder_path).unwrap();
            holder_barrier.wait();
            std::thread::sleep(std::time::Duration::from_millis(300));
            drop(guard);
        });
        barrier.wait();
        let started = std::time::Instant::now();
        assert_eq!(CounterStore::new(temp.path.clone()).increment("held"), None);
        assert!(started.elapsed() < std::time::Duration::from_secs(1));
        holder.join().unwrap();
    }

    #[test]
    fn irrelevant_input_is_silent_noop() {
        let temp = TestTempDir::new();
        let store = CounterStore::new(temp.path.clone());
        assert_eq!(
            process_input(&input("SessionStart", "session"), "codex", &store),
            None
        );
        assert!(fs::read_dir(&temp.path).unwrap().next().is_none());
    }
    #[test]
    fn reminder_occurs_on_every_tenth_turn() {
        let temp = TestTempDir::new();
        let store = CounterStore::new(temp.path.clone());
        let args = input("UserPromptSubmit", "session");
        for turn in 1..=20 {
            let reminder = process_input(&args, "codex", &store);
            if turn % 10 == 0 {
                assert_eq!(reminder.as_deref(), Some(REMINDER));
            } else {
                assert_eq!(reminder, None);
            }
        }
    }

    #[test]
    fn oversize_input_is_silent_noop() {
        let temp = TestTempDir::new();
        let store = CounterStore::new(temp.path.clone());
        let input = vec![b' '; MAX_INPUT_BYTES + 1];
        assert_eq!(process_input(&input, "codex", &store), None);
        assert!(fs::read_dir(&temp.path).unwrap().next().is_none());
    }
    #[test]
    fn concurrent_increments_are_not_lost() {
        let temp = Arc::new(TestTempDir::new());
        let barrier = Arc::new(Barrier::new(16));
        let mut workers = Vec::new();
        for _ in 0..16 {
            let temp = Arc::clone(&temp);
            let barrier = Arc::clone(&barrier);
            workers.push(std::thread::spawn(move || {
                let store = CounterStore::new(temp.path.clone());
                let args = input("UserPromptSubmit", "concurrent-session");
                barrier.wait();
                process_input(&args, "codex", &store)
            }));
        }
        let reminders = workers
            .into_iter()
            .filter_map(|worker| worker.join().unwrap())
            .count();
        assert_eq!(reminders, 1);
        let counter = temp.path.join(format!(
            "relay-hook-{}.count",
            session_hash("concurrent-session")
        ));
        assert_eq!(fs::read_to_string(counter).unwrap(), "16");
    }
}

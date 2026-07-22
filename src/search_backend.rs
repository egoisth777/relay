use serde_json::Value;
use std::env;
use std::ffi::{OsStr, OsString};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{sync_channel, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
use std::mem::{size_of, zeroed};
#[cfg(windows)]
use std::ptr::null;
#[cfg(windows)]
use windows_sys::Win32::Foundation::{CloseHandle, FALSE, HANDLE, INVALID_HANDLE_VALUE};
#[cfg(windows)]
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{
    OpenProcess, OpenThread, ResumeThread, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    THREAD_SUSPEND_RESUME,
};

const SEMBLE_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_STDOUT_BYTES: usize = 1024 * 1024;
const READ_BUFFER_BYTES: usize = 8 * 1024;

/// Run the optional Semble semantic-search tier and decorate matching records.
///
/// Semble is intentionally optional: failures to discover or execute it are
/// represented by an empty result so callers can continue with another tier.
pub fn search_semble(root: &Path, records: &[Value], query: &str, limit: usize) -> Vec<Value> {
    let Ok(stdout) = run_semble(root, query, limit) else {
        return Vec::new();
    };
    parse_semble_output(root, records, &stdout, limit)
}

/// Return whether a bare command name resolves from the current PATH.
pub fn tool_available(name: &str) -> bool {
    let Some(path) = env::var_os("PATH") else {
        return false;
    };
    let extensions: &[&str] = if cfg!(windows) { &[".exe"] } else { &[""] };
    env::split_paths(&path).any(|directory| {
        extensions
            .iter()
            .any(|extension| directory.join(format!("{name}{extension}")).is_file())
    })
}

#[derive(Debug)]
enum CommandError {
    NotFound,
    Failed,
}

fn run_semble(root: &Path, query: &str, limit: usize) -> Result<String, CommandError> {
    let args = semble_args(root, query, limit);
    let timeout = env::var("RELAY_SEMBLE_TIMEOUT")
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|seconds| seconds.is_finite() && *seconds > 0.0)
        .map(Duration::from_secs_f64)
        .unwrap_or(SEMBLE_TIMEOUT);

    match run_command(OsStr::new("semble"), &args, timeout) {
        Ok(stdout) => Ok(stdout),
        Err(CommandError::NotFound) if use_uvx_semble() => {
            let mut uvx_args = Vec::with_capacity(args.len() + 1);
            uvx_args.push(OsString::from("semble"));
            uvx_args.extend(args);
            run_command(OsStr::new("uvx"), &uvx_args, timeout)
        }
        Err(error) => Err(error),
    }
}

pub fn use_uvx_semble() -> bool {
    uvx_semble_opted_in(env::var("RELAY_USE_UVX_SEMBLE").ok().as_deref())
}

fn uvx_semble_opted_in(value: Option<&str>) -> bool {
    value == Some("1")
}

fn semble_args(root: &Path, query: &str, limit: usize) -> Vec<OsString> {
    vec![
        OsString::from("search"),
        OsString::from("-k"),
        OsString::from(limit.to_string()),
        OsString::from("--content"),
        OsString::from("docs"),
        OsString::from("--"),
        OsString::from(query),
        root.join("convs").into_os_string(),
    ]
}

/// Execute a program without a shell, collecting bounded stdout and discarding
/// stderr while enforcing a wall-clock timeout through output collection too.
/// The reader is intentionally never joined: a descendant can retain an
/// inherited stdout pipe after the direct child exits.
fn run_command(
    program: &OsStr,
    args: &[OsString],
    timeout: Duration,
) -> Result<String, CommandError> {
    let (mut child, containment) = spawn_contained(program, args)?;

    let Some(stdout) = child.stdout.take() else {
        stop_contained(&mut child, containment);
        return Err(CommandError::Failed);
    };
    let (stdout_sender, stdout_receiver) = sync_channel(1);
    if thread::Builder::new()
        .spawn(move || {
            let _ = stdout_sender.send(read_stream_bounded(stdout, MAX_STDOUT_BYTES));
        })
        .is_err()
    {
        stop_contained(&mut child, containment);
        return Err(CommandError::Failed);
    }

    let deadline = Instant::now() + timeout;
    let mut status = None;
    let mut stdout = None;
    loop {
        if stdout.is_none() {
            match stdout_receiver.try_recv() {
                Ok(Ok(bytes)) => stdout = Some(bytes),
                Ok(Err(_)) | Err(TryRecvError::Disconnected) => {
                    stop_contained(&mut child, containment);
                    return Err(CommandError::Failed);
                }
                Err(TryRecvError::Empty) => {}
            }
        }

        if status.is_none() {
            match child.try_wait() {
                Ok(Some(exit_status)) => status = Some(exit_status),
                Ok(None) => {}
                Err(_) => {
                    stop_contained(&mut child, containment);
                    return Err(CommandError::Failed);
                }
            }
        }

        if let Some(exit_status) = status.as_ref() {
            if !exit_status.success() {
                stop_contained(&mut child, containment);
                return Err(CommandError::Failed);
            }
            if let Some(bytes) = stdout.take() {
                let decoded = String::from_utf8(bytes).map_err(|_| CommandError::Failed);
                stop_contained(&mut child, containment);
                return decoded;
            }
        }

        let now = Instant::now();
        if now >= deadline {
            stop_contained(&mut child, containment);
            return Err(CommandError::Failed);
        }
        thread::sleep(deadline.duration_since(now).min(Duration::from_millis(10)));
    }
}

#[cfg(unix)]
struct ProcessContainment {
    process_group: i32,
}

#[cfg(windows)]
struct ProcessContainment {
    job: JobHandle,
}

fn spawn_contained(
    program: &OsStr,
    args: &[OsString],
) -> Result<(Child, ProcessContainment), CommandError> {
    let mut command = Command::new(program);
    command
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    #[cfg(unix)]
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_SUSPENDED);

    let mut child = command.spawn().map_err(|error| {
        if error.kind() == io::ErrorKind::NotFound {
            CommandError::NotFound
        } else {
            CommandError::Failed
        }
    })?;

    #[cfg(unix)]
    {
        let process_group = child.id() as i32;
        return Ok((child, ProcessContainment { process_group }));
    }

    #[cfg(windows)]
    {
        let job = match create_windows_job(child.id()) {
            Ok(job) => job,
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(CommandError::Failed);
            }
        };
        Ok((child, ProcessContainment { job }))
    }
}

fn stop_contained(child: &mut Child, containment: ProcessContainment) {
    #[cfg(unix)]
    unsafe {
        let _ = libc::kill(-containment.process_group, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        unsafe {
            let _ = TerminateJobObject(containment.job.0 .0, 1);
        }
        let _ = child.kill();
    }
    let _ = child.wait();
    drop(containment);
}

#[cfg(windows)]
const CREATE_SUSPENDED: u32 = 0x0000_0004;

#[cfg(windows)]
struct NativeHandle(HANDLE);

#[cfg(windows)]
impl Drop for NativeHandle {
    fn drop(&mut self) {
        unsafe {
            if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
                let _ = CloseHandle(self.0);
            }
        }
    }
}

#[cfg(windows)]
struct JobHandle(NativeHandle);

#[cfg(windows)]
fn create_windows_job(process_id: u32) -> io::Result<JobHandle> {
    unsafe {
        let job = CreateJobObjectW(null(), null());
        if job.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = JobHandle(NativeHandle(job));
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job.0 .0,
            JobObjectExtendedLimitInformation,
            &mut limits as *mut _ as *mut _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }

        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, FALSE, process_id);
        if process.is_null() {
            return Err(io::Error::last_os_error());
        }
        let process = NativeHandle(process);
        if AssignProcessToJobObject(job.0 .0, process.0) == 0 {
            return Err(io::Error::last_os_error());
        }
        resume_windows_primary_thread(process_id)?;
        Ok(job)
    }
}

#[cfg(windows)]
unsafe fn resume_windows_primary_thread(process_id: u32) -> io::Result<()> {
    let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let snapshot = NativeHandle(snapshot);
    let mut entry: THREADENTRY32 = zeroed();
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    if Thread32First(snapshot.0, &mut entry) == 0 {
        return Err(io::Error::last_os_error());
    }

    loop {
        if entry.th32OwnerProcessID == process_id {
            let thread = OpenThread(THREAD_SUSPEND_RESUME, FALSE, entry.th32ThreadID);
            if thread.is_null() {
                return Err(io::Error::last_os_error());
            }
            let thread = NativeHandle(thread);
            if ResumeThread(thread.0) == u32::MAX {
                return Err(io::Error::last_os_error());
            }
            return Ok(());
        }
        if Thread32Next(snapshot.0, &mut entry) == 0 {
            break;
        }
    }
    Err(io::Error::other(
        "suspended child primary thread was not found",
    ))
}

fn read_stream_bounded<R: Read>(mut stream: R, limit: usize) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(limit.min(READ_BUFFER_BYTES));
    let mut buffer = [0u8; READ_BUFFER_BYTES];
    loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            return Ok(bytes);
        }
        if count > limit.saturating_sub(bytes.len()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "stdout exceeds configured limit",
            ));
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
}

fn parse_semble_output(root: &Path, records: &[Value], stdout: &str, limit: usize) -> Vec<Value> {
    let normalized_stdout = normalize_separators(stdout);
    let mut basename_counts = std::collections::HashMap::<String, usize>::new();
    for record in records {
        if let Some(file) = record.get("file").and_then(Value::as_str) {
            if let Some(name) = Path::new(&normalize_separators(file))
                .file_name()
                .and_then(|value| value.to_str())
            {
                *basename_counts.entry(name.to_string()).or_default() += 1;
            }
        }
    }
    let mut hits: Vec<(usize, usize, Value)> = records
        .iter()
        .enumerate()
        .filter_map(|(record_index, record)| {
            let file = record.get("file")?.as_str()?;
            let normalized = normalize_separators(file);
            let basename = Path::new(&normalized).file_name()?.to_str()?;
            let position = record_match_position(
                root,
                record,
                &normalized_stdout,
                basename_counts.get(basename).copied() == Some(1),
            )?;
            let score = 10_000usize.saturating_sub(position).max(1);
            let mut decorated = record.as_object()?.clone();
            decorated.insert("layer".into(), Value::String("semble".into()));
            decorated.insert("score".into(), Value::from(score));
            Some((score, record_index, Value::Object(decorated)))
        })
        .collect();

    // Python's stable reverse score sort keeps input order for clamped ties.
    hits.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));

    hits.into_iter()
        .take(limit)
        .map(|(_, _, record)| record)
        .collect()
}

fn record_match_position(
    root: &Path,
    record: &Value,
    stdout: &str,
    allow_basename: bool,
) -> Option<usize> {
    let file = record.get("file")?.as_str()?;
    let absolute = absolute_record_path(root, file);
    let normalized_file = normalize_separators(file);
    let normalized_absolute = normalize_separators(&absolute);
    let mut candidates = vec![
        file,
        normalized_file.as_str(),
        absolute.as_str(),
        normalized_absolute.as_str(),
    ];
    if allow_basename {
        candidates.push(Path::new(&normalized_file).file_name()?.to_str()?);
    }
    candidates
        .iter()
        .filter_map(|candidate| character_position(stdout, candidate))
        .min()
}

fn character_position(stdout: &str, candidate: &str) -> Option<usize> {
    let byte_position = stdout.find(candidate)?;
    Some(stdout[..byte_position].chars().count())
}

fn normalize_separators(path: &str) -> String {
    path.replace('\\', "/")
}

fn absolute_record_path(root: &Path, file: &str) -> String {
    let joined = root.join(file);
    let absolute = if joined.is_absolute() {
        joined
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(joined)
    };
    display_absolute_path(absolute.canonicalize().unwrap_or(absolute))
}

fn display_absolute_path(path: PathBuf) -> String {
    let text = path.to_string_lossy().into_owned();
    #[cfg(windows)]
    {
        if let Some(rest) = text.strip_prefix("\\\\?\\UNC\\") {
            return format!("\\\\{rest}");
        }
        if let Some(rest) = text.strip_prefix("\\\\?\\") {
            return rest.to_string();
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Cursor;

    #[test]
    fn uvx_semble_opt_in_requires_canonical_exact_value() {
        assert!(uvx_semble_opted_in(Some("1")));
        assert!(!uvx_semble_opted_in(None));
        assert!(!uvx_semble_opted_in(Some("0")));
        assert!(!uvx_semble_opted_in(Some("true")));
        assert!(!uvx_semble_opted_in(Some(" 1")));
    }

    #[test]
    fn stdout_matches_paths_and_ranks_by_earliest_position() {
        let root = Path::new("/tmp/relay");
        let records = vec![
            json!({"id":"alpha","file":"convs/alpha.md"}),
            json!({"id":"beta","file":"convs/nested/beta.md"}),
            json!({"id":"gamma","file":"convs/gamma.md"}),
        ];
        let stdout = "convs/nested/beta.md\n... alpha.md ...\n";

        let hits = parse_semble_output(root, &records, stdout, 10);

        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0]["id"], "beta");
        assert_eq!(hits[0]["layer"], "semble");
        assert_eq!(hits[0]["score"], 10_000);
        assert_eq!(hits[1]["id"], "alpha");
        assert_eq!(hits[1]["score"], 9_975);
    }

    #[test]
    fn stdout_matches_basename_and_absolute_path_and_honors_limit() {
        let root = Path::new("relay");
        let records = vec![
            json!({"id":"one","file":"convs/one.md"}),
            json!({"id":"two","file":"convs/two.md"}),
        ];
        let absolute = absolute_record_path(root, "convs/two.md");
        let stdout = format!("{absolute}\none.md\n");

        let hits = parse_semble_output(root, &records, &stdout, 1);

        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0]["id"], "two");
        assert_eq!(hits[0]["layer"], "semble");
        assert!(hits[0]["score"].as_u64().unwrap() > 0);
    }

    #[test]
    fn ranking_uses_unicode_character_positions() {
        let records = vec![
            json!({"id":"alpha","file":"convs/alpha.md"}),
            json!({"id":"beta","file":"convs/beta.md"}),
        ];
        let hits =
            parse_semble_output(Path::new("/tmp/relay"), &records, "é alpha.md\nbeta.md", 10);

        assert_eq!(hits[0]["id"], "alpha");
        assert_eq!(hits[0]["score"], 9_998);
        assert_eq!(hits[1]["score"], 9_989);
    }

    #[test]
    fn clamped_scores_preserve_record_order() {
        let records = vec![
            json!({"id":"one","file":"convs/one.md"}),
            json!({"id":"two","file":"convs/two.md"}),
        ];
        let stdout = format!("{}two.mdone.md", "x".repeat(10_001));
        let hits = parse_semble_output(Path::new("/tmp/relay"), &records, &stdout, 10);

        assert_eq!(hits[0]["id"], "one");
        assert_eq!(hits[1]["id"], "two");
        assert_eq!(hits[0]["score"], 1);
        assert_eq!(hits[1]["score"], 1);
    }

    #[test]
    fn unmatched_records_and_zero_limit_return_no_hits() {
        let records = vec![json!({"id":"one","file":"convs/one.md"})];
        assert!(parse_semble_output(Path::new("/tmp/relay"), &records, "other.md", 10).is_empty());
        assert!(parse_semble_output(Path::new("/tmp/relay"), &records, "one.md", 0).is_empty());
    }
    #[test]
    fn semble_args_end_options_before_untrusted_query() {
        let root = Path::new("root");
        let args = semble_args(root, "--content=unexpected", 7);

        assert_eq!(
            args,
            vec![
                OsString::from("search"),
                OsString::from("-k"),
                OsString::from("7"),
                OsString::from("--content"),
                OsString::from("docs"),
                OsString::from("--"),
                OsString::from("--content=unexpected"),
                root.join("convs").into_os_string(),
            ]
        );
    }

    #[test]
    fn stdout_collection_rejects_data_over_configured_bound() {
        let input = vec![b'x'; MAX_STDOUT_BYTES + 1];
        let error = read_stream_bounded(Cursor::new(input), MAX_STDOUT_BYTES).unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn stdout_collection_accepts_data_at_configured_bound() {
        let input = vec![b'x'; MAX_STDOUT_BYTES];
        let output = read_stream_bounded(Cursor::new(input), MAX_STDOUT_BYTES).unwrap();

        assert_eq!(output.len(), MAX_STDOUT_BYTES);
    }
}

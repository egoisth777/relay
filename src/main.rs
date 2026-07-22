mod atomic_io;
mod hook_runtime;
mod search_backend;

use atomic_io::{
    lock_exclusive, lock_shared, remove_durable, write_atomic, ExclusiveLock, SharedLock,
};
use search_backend::{search_semble, tool_available, use_uvx_semble};

use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Barrier, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::fs::MetadataExt;

const STATUSES: &[&str] = &["active", "parked", "closed"];
const MANDATORY: &[&str] = &["summary", "glossary", "qa"];
const ALWAYS: &[&str] = &["resume", "user-instructions", "condensed-transcript"];
const ORDER: &[&str] = &[
    "summary",
    "glossary",
    "qa",
    "sources",
    "insights",
    "decisions",
    "environment",
    "artifacts",
    "digest",
    "resume",
    "user-instructions",
    "condensed-transcript",
];
const NONE: &str = "(none)";
const GITIGNORE: &str = ".semble/\nindex.jsonl\n__pycache__/\n";
const RELS: &[&str] = &[
    "spawned-from",
    "spawned-to",
    "continued-from",
    "continued-as",
    "informed-by",
    "informed",
];

#[derive(Debug)]
struct ConvError(String);
impl From<std::io::Error> for ConvError {
    fn from(e: std::io::Error) -> Self {
        ConvError(e.to_string())
    }
}
impl std::fmt::Display for ConvError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[derive(Clone, Debug)]
struct Conv {
    path: PathBuf,
    meta: BTreeMap<String, Value>,
    body: String,
}
fn err<S: Into<String>>(s: S) -> ConvError {
    ConvError(s.into())
}
fn now_utc() -> String {
    #[cfg(debug_assertions)]
    if env::var("RELAY_TEST_MODE").as_deref() == Ok("1") {
        if let Ok(value) = env::var("RELAY_TEST_NOW") {
            return value;
        }
    }
    // UTC seconds formatted without external time crate; sufficient ISO shape.
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = secs / 86400;
    let rem = secs % 86400;
    let mut y: i64 = 1970;
    let mut d = days as i64;
    while d >= if leap(y) { 366 } else { 365 } {
        d -= if leap(y) { 366 } else { 365 };
        y += 1;
    }
    let md = [
        31,
        if leap(y) { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut m = 1;
    let mut x = d as i32;
    for n in md {
        if x >= n {
            x -= n;
            m += 1;
        } else {
            break;
        }
    }
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        y,
        m,
        x + 1,
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}
fn leap(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}
fn root_from(arg: Option<&String>) -> Result<PathBuf, ConvError> {
    let p = arg
        .map(|s| {
            let q = PathBuf::from(s);
            if q.is_absolute() {
                q
            } else {
                env::current_dir().unwrap_or_default().join(q)
            }
        })
        .map(Ok)
        .unwrap_or_else(|| {
            env::var_os("HOME")
                .or_else(|| env::var_os("USERPROFILE"))
                .map(PathBuf::from)
                .map(|home| home.join(".relay"))
                .ok_or_else(|| {
                    err("cannot resolve Plugin installation root: platform home is unavailable")
                })
        })?;
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            x => out.push(x.as_os_str()),
        }
    }
    Ok(out)
}
fn convs(root: &Path) -> PathBuf {
    root.join("convs")
}
fn index_path(root: &Path) -> PathBuf {
    root.join("index.jsonl")
}
fn ensure(root: &Path) -> Result<(), ConvError> {
    if root.exists() && !root.is_dir() {
        return Err(err(format!(
            "Plugin installation root must be a directory, not a file: {}",
            root.display()
        )));
    };
    fs::create_dir_all(convs(root))?;
    fs::create_dir_all(root.join(".semble"))?;
    if !index_path(root).exists() {
        write_atomic(&index_path(root), b"")?
    };
    Ok(())
}
fn write_gitignore(root: &Path) -> Result<(), ConvError> {
    let p = root.join(".gitignore");
    if !p.exists() {
        write_atomic(&p, GITIGNORE.as_bytes())?;
    }
    Ok(())
}
fn repair_gitignore(root: &Path) -> Result<bool, ConvError> {
    let path = root.join(".gitignore");
    let current = fs::read_to_string(&path).ok();
    if current.as_deref() == Some(GITIGNORE) {
        return Ok(false);
    }
    write_atomic(&path, GITIGNORE.as_bytes())?;
    Ok(true)
}
fn is_link_or_reparse(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        return metadata.file_attributes() & 0x400 != 0;
    }
    #[cfg(not(windows))]
    false
}
fn mutation_lock(root: &Path) -> Result<ExclusiveLock, ConvError> {
    if root.exists() && !root.is_dir() {
        return Err(err(format!(
            "Plugin installation root must be a directory, not a file: {}",
            root.display()
        )));
    }
    let cache = root.join(".semble");
    fs::create_dir_all(&cache)?;
    let lock_path = cache.join("write.lock");
    if let Ok(metadata) = fs::symlink_metadata(&lock_path) {
        if is_link_or_reparse(&metadata) || !metadata.is_file() {
            return Err(err(
                "write.lock must be a regular file inside the Plugin installation root",
            ));
        }
    }
    trace_event(json!({"event":"lock_wait","mode":"exclusive"}));
    let lock = lock_exclusive(&lock_path)?;
    trace_event(json!({"event":"lock_acquire","mode":"exclusive"}));
    lock_barrier()?;
    Ok(lock)
}

fn reader_lock(root: &Path) -> Result<Option<SharedLock>, ConvError> {
    let path = root.join(".semble").join("write.lock");
    if !path.is_file() {
        return Ok(None);
    }
    let metadata = fs::symlink_metadata(&path)?;
    if is_link_or_reparse(&metadata) || !metadata.is_file() {
        return Err(err(
            "write.lock must be a regular file inside the Plugin installation root",
        ));
    }
    trace_event(json!({"event":"lock_wait","mode":"shared"}));
    let lock = lock_shared(&path)?;
    trace_event(json!({"event":"lock_acquire","mode":"shared"}));
    lock_barrier()?;
    Ok(Some(lock))
}
fn read_lock_with_recovery(root: &Path) -> Result<Option<SharedLock>, ConvError> {
    loop {
        let shared = reader_lock(root)?;
        if journal_path(root).is_file() {
            drop(shared);
            let recovery_lock = mutation_lock(root)?;
            recover_journal(root)?;
            drop(recovery_lock);
            continue;
        }
        return Ok(shared);
    }
}

fn lock_barrier() -> Result<(), ConvError> {
    #[cfg(debug_assertions)]
    if env::var("RELAY_TEST_MODE").as_deref() == Ok("1") {
        if let Some(base) = env::var_os("RELAY_TEST_BARRIER_AFTER_LOCK") {
            let base = PathBuf::from(base);
            let ready = PathBuf::from(format!("{}.{}.ready", base.display(), process::id()));
            let release = PathBuf::from(format!("{}.release", base.display()));
            fs::write(&ready, b"ready")?;
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
            while !release.exists() {
                if std::time::Instant::now() >= deadline {
                    return Err(err(
                        "timed out waiting for RELAY_TEST_BARRIER_AFTER_LOCK release",
                    ));
                }
                thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    }
    Ok(())
}

fn valstr(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
        None => String::new(),
    }
}
fn iso(v: Option<&Value>) -> String {
    if let Some(Value::String(s)) = v {
        s.clone()
    } else {
        valstr(v)
    }
}
fn json_quote(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into())
}
fn slugify(t: &str) -> String {
    let mut s = String::new();
    for c in t.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            s.push(c)
        } else if !s.ends_with('-') {
            s.push('-')
        }
    }
    while s.ends_with('-') {
        s.pop();
    }
    if s.is_empty() {
        "conversation".into()
    } else {
        s.chars().take(64).collect()
    }
}
fn make_id(topic: &str) -> String {
    let n = now_utc();
    format!(
        "conv_{}_{}",
        n[2..10].replace(['-', ':'], ""),
        slugify(topic)
    )
}
fn portable(s: &str, kind: &str) -> Result<(), ConvError> {
    if s.is_empty()
        || s.trim() != s
        || s.ends_with('.')
        || s.ends_with(' ')
        || s == "."
        || s == ".."
        || s.chars().any(|c| "<>:\"/\\|?*".contains(c) || c < ' ')
    {
        return Err(err(format!(
            "{} must be a portable filename component",
            kind
        )));
    };
    let stem = s.split('.').next().unwrap_or("").to_ascii_uppercase();
    if [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ]
    .contains(&stem.as_str())
    {
        return Err(err(format!(
            "{} must be a portable filename component",
            kind
        )));
    };
    Ok(())
}
fn valid_id(id: &str) -> Result<(), ConvError> {
    if id.contains('/') || id.contains('\\') {
        return Err(err(
            "relay record id must produce a file inside the Relay archive",
        ));
    };
    portable(id, "relay record id")
}
fn filename(id: &str) -> String {
    let bytes = id.as_bytes();
    if bytes.len() > 12
        && bytes.get(..5) == Some(b"conv_")
        && bytes[5..11].iter().all(u8::is_ascii_digit)
        && bytes[11] == b'_'
    {
        format!(
            "20{}-{}-{}_{}.md",
            &id[5..7],
            &id[7..9],
            &id[9..11],
            &id[12..]
        )
    } else {
        format!("{}.md", id)
    }
}
fn path_for(root: &Path, id: &str) -> Result<PathBuf, ConvError> {
    valid_id(id)?;
    let f = filename(id);
    portable(&f, "relay record id")?;
    Ok(convs(root).join(f))
}
fn split_front(text: &str, path: &Path) -> Result<(String, String), ConvError> {
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    if lines.first().map(|s| s.trim()) != Some("+++") {
        return Err(err(format!(
            "{} is missing TOML +++ frontmatter",
            path.display()
        )));
    };
    for i in 1..lines.len() {
        if lines[i].trim() == "+++" {
            return Ok((lines[1..i].concat(), lines[i + 1..].concat()));
        }
    }
    Err(err(format!(
        "{} has unterminated TOML frontmatter",
        path.display()
    )))
}
fn read_conv(path: &Path) -> Result<Conv, ConvError> {
    let bytes = fs::read(path)?;
    read_conv_bytes(path, &bytes)
}
fn read_conv_bytes(path: &Path, bytes: &[u8]) -> Result<Conv, ConvError> {
    let text = std::str::from_utf8(bytes)
        .map_err(|error| err(format!("{} is not valid UTF-8: {}", path.display(), error)))?;
    let (front, body) = split_front(&text, path)?;
    let tv: toml::Value = front.parse().map_err(|e| {
        err(format!(
            "{} has invalid TOML frontmatter: {}",
            path.display(),
            e
        ))
    })?;
    let mut m = BTreeMap::new();
    if let toml::Value::Table(t) = tv {
        for (k, v) in t {
            m.insert(k, toml_to_json(v));
        }
    };
    for k in ["id", "topic", "status"] {
        if !m.contains_key(k) {
            return Err(err(format!("{} frontmatter missing {}", path.display(), k)));
        }
    }
    if !m.contains_key("refs") {
        m.insert("refs".into(), json!([]));
    }
    if !m.contains_key("tags") {
        m.insert("tags".into(), json!([]));
    }
    Ok(Conv {
        path: path.to_path_buf(),
        meta: m,
        body: body.to_string(),
    })
}
fn toml_to_json(v: toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::String(s),
        toml::Value::Integer(i) => json!(i),
        toml::Value::Float(f) => json!(f),
        toml::Value::Boolean(b) => json!(b),
        toml::Value::Datetime(d) => Value::String(d.to_string()),
        toml::Value::Array(a) => Value::Array(a.into_iter().map(toml_to_json).collect()),
        toml::Value::Table(t) => {
            Value::Object(t.into_iter().map(|(k, v)| (k, toml_to_json(v))).collect())
        }
    }
}
fn all_convs(root: &Path, tolerate: bool) -> Result<Vec<Conv>, ConvError> {
    ensure(root)?;
    let engine = ScanEngine::configured()?;
    let snapshot = engine.snapshot(root)?;
    Ok(engine
        .parse_files(root, &snapshot, tolerate)?
        .into_iter()
        .map(|(_, conv, _)| conv)
        .collect())
}
fn id(c: &Conv) -> String {
    valstr(c.meta.get("id"))
}
fn find(root: &Path, target: &str) -> Result<Option<Conv>, ConvError> {
    if valid_id(target).is_ok() {
        let direct = path_for(root, target)?;
        if direct.is_file() {
            let conv = read_conv(&direct)?;
            trace_record(
                "record_open",
                root,
                &direct,
                0,
                fs::metadata(&direct)
                    .map(|metadata| metadata.len() as usize)
                    .unwrap_or(0),
            );
            if id(&conv) == target {
                return Ok(Some(conv));
            }
            return Err(err(format!(
                "conversation file collision for {}: {} has frontmatter id {}",
                target,
                direct.display(),
                id(&conv)
            )));
        }
        for record in read_index(root, true)? {
            if valstr(record.get("id")) != target {
                continue;
            }
            let path = root.join(valstr(record.get("file")));
            if path.is_file() {
                let conv = read_conv(&path)?;
                if id(&conv) == target {
                    return Ok(Some(conv));
                }
            }
            break;
        }
    }
    for conv in all_convs(root, true)? {
        if id(&conv) == target {
            return Ok(Some(conv));
        }
    }
    Ok(None)
}
fn normalize_refs(v: Option<&Value>) -> Result<Vec<Value>, ConvError> {
    let Some(Value::Array(a)) = v else {
        return Ok(vec![]);
    };
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for r in a {
        let Some(o) = r.as_object() else {
            return Err(err(format!("ref must be an object: {}", r)));
        };
        let rid = valstr(o.get("id"));
        let rel = valstr(o.get("rel"));
        if rid.is_empty() || rel.is_empty() {
            return Err(err(format!("invalid ref: {}", r)));
        };
        valid_id(&rid)?;
        if !RELS.contains(&rel.as_str()) {
            return Err(err(format!(
                "unknown ref rel {:?}; expected one of {:?}",
                rel, RELS
            )));
        };
        if seen.insert((rid.clone(), rel.clone())) {
            out.push(json!({"id":rid,"rel":rel}));
        }
    }
    out.sort_by_key(|v| (valstr(v.get("id")), valstr(v.get("rel"))));
    Ok(out)
}
fn section_headers(body: &str) -> Vec<(usize, usize, String)> {
    let mut out = Vec::new();
    let mut offset = 0usize;
    for line in body.split_inclusive('\n') {
        let content = line
            .strip_suffix('\n')
            .unwrap_or(line)
            .strip_suffix('\r')
            .unwrap_or(line.strip_suffix('\n').unwrap_or(line));
        if let Some(rest) = content.strip_prefix("##") {
            if rest.chars().next().is_some_and(char::is_whitespace) {
                let name = rest.trim().to_lowercase();
                if !name.is_empty() {
                    out.push((offset, offset + line.len(), name));
                }
            }
        }
        offset += line.len();
    }
    out
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FileStat {
    path: PathBuf,
    relative: String,
    size: u64,
    mtime_ns: u64,
}

#[derive(Clone, Copy, Debug)]
struct ScanEngine {
    workers: usize,
}

static TRACE_LOCK: Mutex<()> = Mutex::new(());

fn trace_event(value: Value) {
    if env::var("RELAY_TEST_MODE").as_deref() != Ok("1") {
        return;
    }
    let Some(path) = env::var_os("RELAY_TEST_TRACE_IO") else {
        return;
    };
    let Ok(_guard) = TRACE_LOCK.lock() else {
        return;
    };
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) else {
        return;
    };
    if let Ok(mut line) = serde_json::to_vec(&value) {
        line.push(b'\n');
        let _ = file.write_all(&line);
    }
}

fn trace_record(event: &str, root: &Path, path: &Path, worker_id: usize, bytes: usize) {
    let relative = path
        .strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    trace_event(json!({"event":event,"path":relative,"worker_id":worker_id,"bytes":bytes}));
}

impl ScanEngine {
    fn configured() -> Result<Self, ConvError> {
        let default = thread::available_parallelism()
            .map(|count| count.get())
            .unwrap_or(1)
            .clamp(1, 8);
        let workers = match env::var("RELAY_SCAN_THREADS") {
            Ok(value) => value
                .parse::<usize>()
                .ok()
                .filter(|count| (1..=64).contains(count))
                .ok_or_else(|| err("RELAY_SCAN_THREADS must be a decimal integer in 1..=64"))?,
            Err(_) => default,
        };
        Ok(Self { workers })
    }

    fn snapshot(&self, root: &Path) -> Result<Vec<FileStat>, ConvError> {
        trace_event(json!({"event":"snapshot"}));
        let archive = convs(root);
        let archive_metadata = fs::symlink_metadata(&archive)?;
        if is_link_or_reparse(&archive_metadata) || !archive_metadata.is_dir() {
            return Err(err(
                "Relay archive must be a real directory, not a symlink or reparse point",
            ));
        }
        let mut files = Vec::new();
        let mut stack = vec![archive.clone()];
        let mut directories = 0u64;
        while let Some(directory) = stack.pop() {
            directories += 1;
            let mut entries = fs::read_dir(&directory)?.collect::<Result<Vec<_>, _>>()?;
            entries.sort_by_key(|entry| entry.file_name());
            for entry in entries {
                let metadata = fs::symlink_metadata(entry.path())?;
                let kind = metadata.file_type();
                if is_link_or_reparse(&metadata) {
                    continue;
                }
                let path = entry.path();
                if kind.is_dir() {
                    stack.push(path);
                    continue;
                }
                if !kind.is_file()
                    || path.extension().and_then(|extension| extension.to_str()) != Some("md")
                {
                    continue;
                }
                let relative_path = path
                    .strip_prefix(root)
                    .map_err(|_| err("Relay archive entry escaped the Plugin installation root"))?;
                let relative = relative_path
                    .to_str()
                    .ok_or_else(|| {
                        err(format!(
                            "Relay archive path is not valid UTF-8: {:?}",
                            relative_path
                        ))
                    })?
                    .replace('\\', "/");
                let mtime_ns = metadata
                    .modified()
                    .ok()
                    .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                    .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
                    .unwrap_or(0);
                files.push(FileStat {
                    path,
                    relative,
                    size: metadata.len(),
                    mtime_ns,
                });
            }
        }
        files.sort_by(|left, right| left.relative.cmp(&right.relative));
        trace_event(json!({"event":"snapshot_end","files":files.len(),"directories":directories}));
        Ok(files)
    }

    fn parse_files(
        &self,
        root: &Path,
        files: &[FileStat],
        tolerate: bool,
    ) -> Result<Vec<(FileStat, Conv, Vec<u8>)>, ConvError> {
        let worker_count = self.workers.max(1);
        trace_event(json!({"event":"scan_start","workers":worker_count}));
        if files.is_empty() {
            trace_event(json!({"event":"scan_end","workers_started":0,"max_active":0}));
            return Ok(Vec::new());
        }
        let cursor = AtomicUsize::new(0);
        let active = AtomicUsize::new(0);
        let max_active = AtomicUsize::new(0);
        let started = AtomicUsize::new(0);
        let output = Mutex::new(Vec::<(FileStat, Result<(Conv, Vec<u8>), ConvError>)>::new());
        let barrier = Barrier::new(worker_count);
        let chunk = files.len().div_ceil(worker_count).clamp(1, 16);
        thread::scope(|scope| {
            for worker_id in 0..worker_count {
                let cursor = &cursor;
                let active = &active;
                let max_active = &max_active;
                let started = &started;
                let output = &output;
                let barrier = &barrier;
                scope.spawn(move || {
                    started.fetch_add(1, Ordering::SeqCst);
                    let current = active.fetch_add(1, Ordering::SeqCst) + 1;
                    max_active.fetch_max(current, Ordering::SeqCst);
                    trace_event(json!({"event":"worker_start","worker_id":worker_id}));
                    barrier.wait();
                    loop {
                        let begin = cursor.fetch_add(chunk, Ordering::SeqCst);
                        if begin >= files.len() {
                            break;
                        }
                        let end = (begin + chunk).min(files.len());
                        for stat in &files[begin..end] {
                            let result =
                                fs::read(&stat.path)
                                    .map_err(ConvError::from)
                                    .and_then(|bytes| {
                                        trace_record(
                                            "record_open",
                                            root,
                                            &stat.path,
                                            worker_id,
                                            bytes.len(),
                                        );
                                        read_conv_bytes(&stat.path, &bytes)
                                            .map(|conv| (conv, bytes))
                                    });
                            if let Ok(mut values) = output.lock() {
                                values.push((stat.clone(), result));
                            }
                        }
                    }
                    active.fetch_sub(1, Ordering::SeqCst);
                    trace_event(json!({"event":"worker_end","worker_id":worker_id}));
                });
            }
        });
        trace_event(json!({
            "event":"scan_end",
            "workers_started":started.load(Ordering::SeqCst),
            "max_active":max_active.load(Ordering::SeqCst),
        }));
        let mut values = output
            .into_inner()
            .map_err(|_| err("scan worker result lock poisoned"))?;
        values.sort_by(|left, right| left.0.relative.cmp(&right.0.relative));
        let mut parsed = Vec::new();
        let mut first_error = None;
        for (stat, result) in values {
            match result {
                Ok((conv, bytes)) => parsed.push((stat, conv, bytes)),
                Err(_error) if tolerate => {}
                Err(error) => {
                    if first_error.is_none() {
                        first_error = Some(error)
                    }
                }
            }
        }
        if let Some(error) = first_error {
            return Err(error);
        }
        let mut by_id: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for (stat, conv, _) in &parsed {
            by_id
                .entry(id(conv))
                .or_default()
                .push(stat.relative.clone());
        }
        let duplicates = by_id
            .into_iter()
            .filter(|(_, paths)| paths.len() > 1)
            .collect::<Vec<_>>();
        if !duplicates.is_empty() && !tolerate {
            let detail = duplicates
                .iter()
                .map(|(record_id, paths)| format!("{}: {}", record_id, paths.join(", ")))
                .collect::<Vec<_>>()
                .join("; ");
            return Err(err(format!("duplicate relay record id(s): {}", detail)));
        }
        if tolerate && !duplicates.is_empty() {
            let duplicate_ids = duplicates
                .into_iter()
                .map(|(record_id, _)| record_id)
                .collect::<HashSet<_>>();
            parsed.retain(|(_, conv, _)| !duplicate_ids.contains(&id(conv)));
        }
        Ok(parsed)
    }
}
fn normalize_section_map(
    mut sections: BTreeMap<String, String>,
    reject_conflict: bool,
) -> Result<BTreeMap<String, String>, ConvError> {
    let dict = sections.remove("dict");
    let glossary = sections.remove("glossary");
    match (dict, glossary) {
        (Some(dict), Some(glossary)) => {
            if dict.trim() != glossary.trim() && reject_conflict {
                return Err(err("conflicting sections: dict and glossary"));
            }
            sections.insert("glossary".into(), glossary);
        }
        (Some(dict), None) => {
            sections.insert("glossary".into(), dict);
        }
        (None, Some(glossary)) => {
            sections.insert("glossary".into(), glossary);
        }
        (None, None) => {}
    }
    Ok(sections)
}
fn sections(body: &str) -> Result<BTreeMap<String, String>, ConvError> {
    let ms = section_headers(body);
    let mut out = BTreeMap::new();
    for i in 0..ms.len() {
        let name = ms[i].2.clone();
        if out.contains_key(&name) {
            return Err(err(format!("duplicate section(s): {}", name)));
        }
        let start = ms[i].1;
        let end = if i + 1 < ms.len() {
            ms[i + 1].0
        } else {
            body.len()
        };
        out.insert(name, body[start..end].trim().to_string());
    }
    normalize_section_map(out, true)
}
fn sections_allow_dup(body: &str) -> Result<BTreeMap<String, String>, ConvError> {
    let ms = section_headers(body);
    let mut out = BTreeMap::new();
    for i in 0..ms.len() {
        let name = ms[i].2.clone();
        let start = ms[i].1;
        let end = if i + 1 < ms.len() {
            ms[i + 1].0
        } else {
            body.len()
        };
        out.insert(name, body[start..end].trim().to_string());
    }
    normalize_section_map(out, true)
}
fn duplicates(body: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut d = HashSet::new();
    for (_, _, n) in section_headers(body) {
        if !seen.insert(n.clone()) {
            d.insert(n);
        }
    }
    let mut v: Vec<_> = d.into_iter().collect();
    v.sort();
    v
}
fn count_open(body: &str) -> i64 {
    let s = sections_allow_dup(body)
        .ok()
        .and_then(|mut sections| sections.remove("qa"))
        .unwrap_or_default();
    s.lines().filter(|line| has_open_marker(line)).count() as i64
}
fn word_boundary(bytes: &[u8], index: usize) -> bool {
    index == 0 || !bytes[index - 1].is_ascii_alphanumeric() && bytes[index - 1] != b'_'
}
fn has_open_marker(line: &str) -> bool {
    let lower = line.to_lowercase();
    let bytes = lower.as_bytes();
    for index in 0..bytes.len() {
        if word_boundary(bytes, index) && bytes[index] == b'q' {
            let mut cursor = index + 1;
            while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                cursor += 1;
            }
            if bytes.get(cursor..cursor + 6) == Some(b"(open)") {
                return true;
            }
        }
        if word_boundary(bytes, index) && bytes.get(index..index + 4) == Some(b"open") {
            let mut cursor = index + 4;
            while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                cursor += 1;
            }
            if bytes.get(cursor) == Some(&b':') {
                return true;
            }
        }
    }
    false
}
fn norm_section(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| valstr(Some(x)).trim_end().to_string())
            .filter(|s| !s.trim().is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        Some(v) => valstr(Some(v)).trim().to_string(),
    }
}
fn items(v: Option<&Value>) -> Vec<String> {
    match v {
        None | Some(Value::Null) => vec![],
        Some(Value::String(s)) => {
            if s.trim().is_empty() {
                vec![]
            } else {
                vec![s.trim().into()]
            }
        }
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| valstr(Some(x)).trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
        Some(v) => vec![valstr(Some(v)).trim().into()],
    }
}
fn render_resume(v: Option<&Value>) -> Result<String, ConvError> {
    let Some(v) = v else { return Ok(String::new()) };
    if v.is_null() {
        return Ok(String::new());
    };
    if let Value::String(s) = v {
        return Ok(s.trim().into());
    };
    let Some(o) = v.as_object() else {
        return Err(err("resume must be an object or a string"));
    };
    let goal = valstr(o.get("goal")).trim().to_string();
    let groups = [
        ("checkpoints", items(o.get("checkpoints"))),
        ("next-steps", items(o.get("next_steps"))),
        ("open-questions", items(o.get("open_questions"))),
        ("suggested-skills", items(o.get("suggested_skills"))),
    ];
    if goal.is_empty() && groups.iter().all(|(_, x)| x.is_empty()) {
        return Ok(String::new());
    };
    let mut l = vec![format!(
        "- goal: {}",
        if goal.is_empty() { NONE.into() } else { goal }
    )];
    for (n, x) in groups {
        l.push(format!("- {}:", n));
        for i in if x.is_empty() { vec![NONE.into()] } else { x } {
            l.push(format!("  - {}", i));
        }
    }
    Ok(l.join("\n"))
}
fn render_ui(v: Option<&Value>) -> String {
    if let Some(Value::String(s)) = v {
        return s.trim().into();
    }
    items(v)
        .into_iter()
        .map(|s| format!("- {}", s))
        .collect::<Vec<_>>()
        .join("\n")
}
fn render_trans(v: Option<&Value>) -> Result<String, ConvError> {
    let Some(v) = v else { return Ok(String::new()) };
    if let Value::String(s) = v {
        return Ok(s.trim().into());
    };
    let Some(a) = v.as_array() else {
        return Err(err("condensed_transcript must be a list"));
    };
    let mut l = Vec::new();
    for e in a {
        if let Some(o) = e.as_object() {
            let weight = match o.get("w") {
                None => 1,
                Some(Value::Number(number)) => number
                    .as_u64()
                    .filter(|value| (1..=3).contains(value))
                    .ok_or_else(|| err("transcript weight must be an integer in 1..=3"))?,
                Some(_) => return Err(err("transcript weight must be an integer in 1..=3")),
            };
            let u = valstr(o.get("u")).trim().to_string();
            let aa = valstr(o.get("a")).trim().to_string();
            if !u.is_empty() || !aa.is_empty() {
                l.push(format!("<!-- relay:transcript-weight={} -->", weight));
            }
            if !u.is_empty() {
                l.push(format!("- U: {}", u))
            };
            if !aa.is_empty() {
                l.push(format!("- A: {}", aa))
            }
        } else {
            let s = valstr(Some(e)).trim().to_string();
            if !s.is_empty() {
                l.push(format!("- {}", s))
            }
        }
    }
    Ok(l.join("\n"))
}
fn canonical(sec: &BTreeMap<String, String>, always: Option<&BTreeMap<String, String>>) -> String {
    let mut parts = Vec::new();
    let mut emitted = HashSet::new();
    for n in ORDER {
        let c = if ALWAYS.contains(n) {
            always
                .and_then(|a| a.get(*n))
                .filter(|x| !x.is_empty())
                .or_else(|| sec.get(*n))
                .map(|s| s.as_str())
                .unwrap_or(NONE)
        } else {
            sec.get(*n).map(|s| s.as_str()).unwrap_or("")
        };
        if c.is_empty() && !ALWAYS.contains(n) {
            continue;
        };
        parts.push(format!("## {}\n{}\n", n, c.trim()));
        emitted.insert(*n);
    }
    for (n, c) in sec {
        if !emitted.contains(n.as_str()) && !c.is_empty() {
            parts.push(format!("## {}\n{}\n", n, c.trim()))
        }
    }
    format!("{}\n", parts.join("\n").trim_end())
}
fn build_body(raw: &Map<String, Value>) -> Result<String, ConvError> {
    let mut always = BTreeMap::new();
    always.insert("resume".into(), render_resume(raw.get("resume"))?);
    always.insert(
        "user-instructions".into(),
        render_ui(raw.get("user_instructions")),
    );
    for name in ["environment", "artifacts"] {
        if raw.contains_key(name) {
            let rendered = render_ui(raw.get(name));
            always.insert(
                name.into(),
                if rendered.is_empty() {
                    NONE.into()
                } else {
                    rendered
                },
            );
        }
    }
    always.insert(
        "condensed-transcript".into(),
        render_trans(raw.get("condensed_transcript"))?,
    );
    let mut sec = BTreeMap::new();
    if let Some(Value::String(b)) = raw.get("body") {
        let b = format!("{}\n", b.trim());
        let s = sections(&b)?;
        for n in MANDATORY {
            if !s.contains_key(*n) {
                return Err(err(format!("body missing mandatory sections: {}", n)));
            }
        }
        let mut merged = s;
        for name in ["environment", "artifacts"] {
            if let Some(value) = always.get(name) {
                merged.insert(name.into(), value.clone());
            }
        }
        return Ok(canonical(&merged, Some(&always)));
    }
    let Some(Value::Object(o)) = raw.get("sections") else {
        return Err(err("sections object is required when body is not provided"));
    };
    for (k, v) in o {
        let s = norm_section(Some(v));
        sec.insert(k.trim().to_lowercase(), s);
    }
    sec = normalize_section_map(sec, true)?;
    for name in ["environment", "artifacts"] {
        if let Some(value) = always.get(name) {
            sec.insert(name.into(), value.clone());
        }
    }
    let miss: Vec<_> = MANDATORY
        .iter()
        .filter(|n| !sec.get(**n).map(|s| !s.is_empty()).unwrap_or(false))
        .cloned()
        .collect();
    if !miss.is_empty() {
        return Err(err(format!(
            "missing mandatory sections: {}",
            miss.join(", ")
        )));
    }
    Ok(canonical(&sec, Some(&always)))
}
fn dump_meta(m: &BTreeMap<String, Value>) -> Result<String, ConvError> {
    let id = valstr(m.get("id"));
    let topic = valstr(m.get("topic"));
    let status = valstr(m.get("status"));
    let tags = items(m.get("tags"))
        .into_iter()
        .map(|s| json_quote(&s))
        .collect::<Vec<_>>()
        .join(", ");
    let refs = normalize_refs(m.get("refs"))?;
    let mut l = vec![
        "+++".into(),
        format!("id = {}", json_quote(&id)),
        format!("topic = {}", json_quote(&topic)),
        format!("status = {}", json_quote(&status)),
        format!("tags = [{}]", tags),
    ];
    if refs.is_empty() {
        l.push("refs = []".into())
    } else {
        l.push("refs = [".into());
        for r in refs {
            l.push(format!(
                "  {{ id = {}, rel = {} }},",
                json_quote(&valstr(r.get("id"))),
                json_quote(&valstr(r.get("rel")))
            ))
        }
        l.push("]".into())
    };
    if m.get("relay_schema").and_then(Value::as_u64) == Some(2) {
        l.push("relay_schema = 2".into());
    }
    l.push(format!("created = {}", iso(m.get("created"))));
    l.push(format!("updated = {}", iso(m.get("updated"))));
    l.extend(["+++".into(), "".into()]);
    Ok(l.join("\n"))
}
fn write_conv(cpath: &Path, m: &BTreeMap<String, Value>, body: &str) -> Result<bool, ConvError> {
    let txt = format!("{}{}\n", dump_meta(m)?, body.trim());
    let old = fs::read_to_string(cpath).ok();
    if old.as_deref() == Some(&txt) {
        return Ok(false);
    };
    if let Some(p) = cpath.parent() {
        fs::create_dir_all(p)?
    };
    write_atomic(cpath, txt.as_bytes())?;
    Ok(true)
}
fn normalize_meta(
    raw: &Map<String, Value>,
    existing: Option<&BTreeMap<String, Value>>,
    root: &Path,
    known_ids: Option<&HashSet<String>>,
) -> Result<BTreeMap<String, Value>, ConvError> {
    let topic = raw
        .get("topic")
        .or_else(|| existing.and_then(|m| m.get("topic")))
        .map(|v| valstr(Some(v)).trim().to_string())
        .unwrap_or_default();
    if topic.is_empty() {
        return Err(err("topic is required"));
    };
    let status = raw
        .get("status")
        .or_else(|| existing.and_then(|m| m.get("status")))
        .map(|v| valstr(Some(v)).trim().to_string())
        .unwrap_or_else(|| "active".into());
    if !STATUSES.contains(&status.as_str()) {
        return Err(err(format!("status must be one of {:?}", STATUSES)));
    };
    let tags = raw
        .get("tags")
        .or_else(|| existing.and_then(|m| m.get("tags")))
        .cloned()
        .unwrap_or(json!([]));
    if !tags.is_array() {
        return Err(err("tags must be a list"));
    };
    let mut ts = items(Some(&tags));
    ts.sort();
    ts.dedup();
    let cid = raw
        .get("id")
        .or_else(|| existing.and_then(|m| m.get("id")))
        .map(|v| valstr(Some(v)))
        .unwrap_or_default();
    let cid = if cid.is_empty() {
        let base = make_id(&topic);
        let mut x = base.clone();
        let mut n = 2;
        while match known_ids {
            Some(ids) => ids.contains(&x),
            None => find(root, &x)?.is_some(),
        } {
            x = format!("{}-{}", base, n);
            n += 1
        }
        x
    } else {
        valid_id(&cid)?;
        cid
    };
    let refs = normalize_refs(
        raw.get("refs")
            .or_else(|| existing.and_then(|m| m.get("refs"))),
    )?;
    let created = raw
        .get("created")
        .or_else(|| existing.and_then(|m| m.get("created")))
        .map(|v| iso(Some(v)))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(now_utc);
    let updated = raw
        .get("updated")
        .map(|v| iso(Some(v)))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(now_utc);
    let mut m = BTreeMap::new();
    m.insert("id".into(), json!(cid));
    m.insert("topic".into(), json!(topic));
    m.insert("status".into(), json!(status));
    m.insert("tags".into(), json!(ts));
    m.insert("refs".into(), Value::Array(refs));
    m.insert("created".into(), json!(created));
    m.insert("updated".into(), json!(updated));
    let structured_transcript = raw
        .get("condensed_transcript")
        .and_then(Value::as_array)
        .is_some_and(|entries| entries.iter().any(Value::is_object));
    if structured_transcript
        || raw.get("relay_schema").and_then(Value::as_u64) == Some(2)
        || existing
            .and_then(|meta| meta.get("relay_schema"))
            .and_then(Value::as_u64)
            == Some(2)
    {
        m.insert("relay_schema".into(), json!(2));
    }
    Ok(m)
}
fn index_record(root: &Path, c: &Conv) -> Result<Value, ConvError> {
    Ok(
        json!({"id":id(c),"topic":valstr(c.meta.get("topic")),"status":valstr(c.meta.get("status")),"tags":c.meta.get("tags").cloned().unwrap_or(json!([])),"refs":normalize_refs(c.meta.get("refs"))?,"created":iso(c.meta.get("created")),"updated":iso(c.meta.get("updated")),"file":c.path.strip_prefix(root).unwrap_or(&c.path).to_string_lossy().replace('\\',"/"),"open":count_open(&c.body)}),
    )
}

#[derive(Clone, Debug)]
struct CacheState {
    rows: Vec<Value>,
    manifest: Option<Value>,
    postings_valid: bool,
}

fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    for byte in bytes {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn hash_text(bytes: &[u8]) -> String {
    format!("{:016x}", fnv1a(bytes))
}

fn index_v2(root: &Path) -> PathBuf {
    root.join(".semble").join("index-v2")
}

fn manifest_path(root: &Path) -> PathBuf {
    index_v2(root).join("manifest.json")
}

fn cache_row(root: &Path, conv: &Conv, stat: &FileStat, bytes: &[u8]) -> Result<Value, ConvError> {
    let mut object = index_record(root, conv)?
        .as_object()
        .cloned()
        .ok_or_else(|| err("index record must be an object"))?;
    object.insert("size".into(), json!(stat.size));
    object.insert("mtime_ns".into(), json!(stat.mtime_ns));
    object.insert("fp".into(), json!(hash_text(bytes)));
    Ok(Value::Object(object))
}

fn compat_row(row: &Value) -> Value {
    let mut object = row.as_object().cloned().unwrap_or_default();
    for key in ["size", "mtime_ns", "fp"] {
        object.remove(key);
    }
    Value::Object(object)
}

fn serialize_jsonl(rows: &[Value]) -> Result<Vec<u8>, ConvError> {
    let mut bytes = Vec::new();
    for row in rows {
        serde_json::to_writer(&mut bytes, row).map_err(|error| err(error.to_string()))?;
        bytes.push(b'\n');
    }
    Ok(bytes)
}

fn compat_rows(rows: &[Value]) -> Vec<Value> {
    let mut result = rows.iter().map(compat_row).collect::<Vec<_>>();
    result.sort_by(|left, right| valstr(left.get("id")).cmp(&valstr(right.get("id"))));
    result
}

fn normalized_cache_file(value: &str) -> bool {
    if !value.starts_with("convs/") || !value.ends_with(".md") || value.contains('\\') {
        return false;
    }
    let segments = value.split('/').collect::<Vec<_>>();
    segments.len() >= 2
        && segments
            .iter()
            .all(|segment| !segment.is_empty() && *segment != "." && *segment != "..")
}

fn cache_row_valid(row: &Value) -> bool {
    let Some(object) = row.as_object() else {
        return false;
    };
    let exact = [
        "created", "file", "fp", "id", "mtime_ns", "open", "refs", "size", "status", "tags",
        "topic", "updated",
    ];
    if object.len() != exact.len() || exact.iter().any(|key| !object.contains_key(*key)) {
        return false;
    }
    object
        .get("file")
        .and_then(Value::as_str)
        .is_some_and(normalized_cache_file)
        && object.get("id").and_then(Value::as_str).is_some()
        && object.get("size").and_then(Value::as_u64).is_some()
        && object.get("mtime_ns").and_then(Value::as_u64).is_some()
        && object
            .get("fp")
            .and_then(Value::as_str)
            .is_some_and(|value| {
                value.len() == 16
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
            })
}

fn safe_named_artifact(directory: &Path, name: &str) -> Option<PathBuf> {
    if name.is_empty()
        || Path::new(name).file_name().and_then(|value| value.to_str()) != Some(name)
        || name.contains('/')
        || name.contains('\\')
        || name.contains("..")
    {
        return None;
    }
    let path = directory.join(name);
    let metadata = fs::symlink_metadata(&path).ok()?;
    if is_link_or_reparse(&metadata) || !metadata.file_type().is_file() {
        return None;
    }
    Some(path)
}

fn hex_gram(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{:02x}", byte)).collect()
}

fn posting_grams(text: &str) -> BTreeSet<[u8; 3]> {
    let lower = text.to_lowercase();
    lower
        .as_bytes()
        .windows(3)
        .map(|window| [window[0], window[1], window[2]])
        .collect()
}

fn build_postings(rows: &[Value]) -> Result<(Vec<u8>, String), ConvError> {
    let mut postings: BTreeMap<(u8, [u8; 3]), BTreeSet<String>> = BTreeMap::new();
    for row in rows {
        let record_id = valstr(row.get("id"));
        let compat = compat_row(row);
        let tier_one = format!("{} {}", record_id, valstr(row.get("file")));
        let tier_two = serde_json::to_string(&compat).map_err(|error| err(error.to_string()))?;
        for (tier, haystack) in [(1u8, tier_one), (2u8, tier_two)] {
            for gram in posting_grams(&haystack) {
                postings
                    .entry((tier, gram))
                    .or_default()
                    .insert(record_id.clone());
            }
        }
    }
    let mut blocks = Vec::new();
    let mut directory = Vec::new();
    for ((tier, gram), ids) in postings {
        let offset = blocks.len();
        let mut block = ids.into_iter().collect::<Vec<_>>().join("\n").into_bytes();
        block.push(b'\n');
        directory.push(json!({
            "t":tier,
            "g":hex_gram(&gram),
            "o":offset,
            "l":block.len(),
            "h":hash_text(&block),
        }));
        blocks.extend(block);
    }
    let directory_bytes = serde_json::to_vec(&directory).map_err(|error| err(error.to_string()))?;
    let directory_hash = hash_text(&directory_bytes);
    let mut bytes = b"RLYPOST2".to_vec();
    bytes.extend((directory_bytes.len() as u64).to_le_bytes());
    bytes.extend(fnv1a(&directory_bytes).to_le_bytes());
    bytes.extend(&directory_bytes);
    bytes.extend(blocks);
    Ok((bytes, directory_hash))
}

fn read_posting_directory(
    path: &Path,
    expected_hash: &str,
    traced: bool,
) -> Result<(Vec<Value>, u64), ConvError> {
    let mut file = fs::File::open(path)?;
    let mut header = [0u8; 24];
    file.read_exact(&mut header)?;
    if &header[..8] != b"RLYPOST2" {
        return Err(err("invalid postings header"));
    }
    let length = u64::from_le_bytes(header[8..16].try_into().unwrap());
    if length > 64 * 1024 * 1024 {
        return Err(err("postings directory is too large"));
    }
    let header_hash = u64::from_le_bytes(header[16..24].try_into().unwrap());
    let mut directory_bytes = vec![0u8; length as usize];
    file.read_exact(&mut directory_bytes)?;
    if fnv1a(&directory_bytes) != header_hash || hash_text(&directory_bytes) != expected_hash {
        return Err(err("postings directory hash mismatch"));
    }
    let directory: Vec<Value> = serde_json::from_slice(&directory_bytes)
        .map_err(|error| err(format!("invalid postings directory: {}", error)))?;
    for entry in &directory {
        let Some(object) = entry.as_object() else {
            return Err(err("invalid postings directory entry"));
        };
        if object.get("t").and_then(Value::as_u64).is_none()
            || object.get("g").and_then(Value::as_str).is_none()
            || object.get("o").and_then(Value::as_u64).is_none()
            || object.get("l").and_then(Value::as_u64).is_none()
            || object.get("h").and_then(Value::as_str).is_none()
        {
            return Err(err("invalid postings directory entry"));
        }
    }
    if traced {
        trace_event(
            json!({"event":"cache_read","artifact":"postings_directory","bytes":24 + directory_bytes.len()}),
        );
    }
    Ok((directory, 24 + length))
}

fn manifest_generation(manifest: Option<&Value>) -> u64 {
    manifest
        .and_then(|value| value.get("generation"))
        .and_then(Value::as_u64)
        .unwrap_or(0)
}

fn manifest_hint(root: &Path) -> Option<Value> {
    let path = manifest_path(root);
    let metadata = fs::symlink_metadata(&path).ok()?;
    if is_link_or_reparse(&metadata) || !metadata.is_file() {
        return None;
    }
    let value: Value = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    (value.get("version").and_then(Value::as_u64) == Some(2)
        && value.get("generation").and_then(Value::as_u64).is_some())
    .then_some(value)
}

fn load_cache(root: &Path) -> Option<CacheState> {
    if env::var("RELAY_NO_CACHE").as_deref() == Ok("1") {
        return None;
    }
    let directory = index_v2(root);
    let manifest_file = safe_named_artifact(&directory, "manifest.json")?;
    let manifest_bytes = fs::read(&manifest_file).ok()?;
    trace_event(json!({"event":"cache_read","artifact":"manifest","bytes":manifest_bytes.len()}));
    let manifest: Value = serde_json::from_slice(&manifest_bytes).ok()?;
    let object = manifest.as_object()?;
    let keys = [
        "version",
        "generation",
        "record_count",
        "records_file",
        "records_hash",
        "postings_base_generation",
        "postings_base",
        "postings_base_directory_hash",
        "postings_deltas",
        "compat_hash",
    ];
    if object.len() != keys.len()
        || keys.iter().any(|key| !object.contains_key(*key))
        || manifest.get("version")?.as_u64()? != 2
    {
        return None;
    }
    let generation = manifest.get("generation")?.as_u64()?;
    let records_name = manifest.get("records_file")?.as_str()?;
    if records_name != format!("records.{}.jsonl", generation) {
        return None;
    }
    let records_path = safe_named_artifact(&directory, records_name)?;
    let records_bytes = fs::read(records_path).ok()?;
    trace_event(json!({"event":"cache_read","artifact":"records","bytes":records_bytes.len()}));
    if hash_text(&records_bytes) != manifest.get("records_hash")?.as_str()? {
        return None;
    }
    let mut rows = Vec::new();
    for line in records_bytes.split(|byte| *byte == b'\n') {
        if line.is_empty() {
            continue;
        }
        let row: Value = serde_json::from_slice(line).ok()?;
        if !cache_row_valid(&row) {
            return None;
        }
        rows.push(row);
    }
    if rows.len() as u64 != manifest.get("record_count")?.as_u64()? {
        return None;
    }
    let mut paths = HashSet::new();
    let mut ids = HashSet::new();
    if rows
        .iter()
        .any(|row| !paths.insert(valstr(row.get("file"))) || !ids.insert(valstr(row.get("id"))))
    {
        return None;
    }
    if rows
        .windows(2)
        .any(|pair| valstr(pair[0].get("file")) >= valstr(pair[1].get("file")))
    {
        return None;
    }
    let base_generation = manifest.get("postings_base_generation")?.as_u64()?;
    let base_name = manifest.get("postings_base")?.as_str()?;
    let mut postings_valid = base_name == format!("postings.base.{}.bin", base_generation);
    if postings_valid {
        postings_valid = safe_named_artifact(&directory, base_name)
            .and_then(|path| {
                read_posting_directory(
                    &path,
                    manifest.get("postings_base_directory_hash")?.as_str()?,
                    false,
                )
                .ok()
            })
            .is_some();
    }
    let deltas = manifest.get("postings_deltas")?.as_array()?;
    let mut previous = base_generation;
    for delta in deltas {
        let Some(delta_object) = delta.as_object() else {
            postings_valid = false;
            break;
        };
        let Some(delta_generation) = delta_object.get("generation").and_then(Value::as_u64) else {
            postings_valid = false;
            break;
        };
        let Some(name) = delta_object.get("file").and_then(Value::as_str) else {
            postings_valid = false;
            break;
        };
        let Some(hash) = delta_object.get("directory_hash").and_then(Value::as_str) else {
            postings_valid = false;
            break;
        };
        if delta_object.len() != 3
            || delta_generation <= previous
            || delta_generation > generation
            || name != format!("postings.delta.{}.bin", delta_generation)
        {
            postings_valid = false;
            break;
        }
        previous = delta_generation;
        if safe_named_artifact(&directory, name)
            .and_then(|path| read_posting_directory(&path, hash, false).ok())
            .is_none()
        {
            postings_valid = false;
            break;
        }
    }
    Some(CacheState {
        rows,
        manifest: Some(manifest),
        postings_valid,
    })
}

fn crash_at(phase: &str) {
    let _ = phase;
    #[cfg(debug_assertions)]
    if env::var("RELAY_TEST_MODE").as_deref() == Ok("1")
        && env::var("RELAY_TEST_CRASH_AT").as_deref() == Ok(phase)
    {
        process::exit(86);
    }
}

fn publish_cache(
    root: &Path,
    rows: &[Value],
    previous: Option<&Value>,
    incremental: bool,
) -> Result<Value, ConvError> {
    if env::var("RELAY_NO_CACHE").as_deref() == Ok("1") {
        let compat = serialize_jsonl(&compat_rows(rows))?;
        write_atomic(&index_path(root), &compat)?;
        trace_event(json!({"event":"compat_index_publish","generation":0}));
        return Ok(previous.cloned().unwrap_or(Value::Null));
    }
    let directory = index_v2(root);
    fs::create_dir_all(&directory)?;
    let generation = manifest_generation(previous).saturating_add(1).max(1);
    let mut sorted_rows = rows.to_vec();
    sorted_rows.sort_by(|left, right| valstr(left.get("file")).cmp(&valstr(right.get("file"))));
    let records_bytes = serialize_jsonl(&sorted_rows)?;
    let records_name = format!("records.{}.jsonl", generation);
    write_atomic(&directory.join(&records_name), &records_bytes)?;
    trace_event(json!({"event":"cache_write","artifact":"records","bytes":records_bytes.len()}));
    crash_at("after_records_cache");

    let (postings_bytes, postings_hash) = build_postings(&sorted_rows)?;
    let old_deltas = previous
        .and_then(|value| value.get("postings_deltas"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    // This implementation's overlay is a complete current posting snapshot, so its
    // byte size necessarily exceeds the 25% compaction threshold. Retain at most one
    // overlay (needed for generation-local corruption recovery), then compact on the
    // next publication instead of allowing write amplification to accumulate.
    let can_delta = incremental && previous.is_some() && old_deltas.is_empty();
    let (base_generation, base_name, base_hash, deltas) = if can_delta {
        let previous = previous.unwrap();
        let delta_name = format!("postings.delta.{}.bin", generation);
        write_atomic(&directory.join(&delta_name), &postings_bytes)?;
        let mut deltas = old_deltas;
        deltas.push(
            json!({"generation":generation,"file":delta_name,"directory_hash":postings_hash}),
        );
        (
            previous
                .get("postings_base_generation")
                .and_then(Value::as_u64)
                .unwrap_or(generation),
            valstr(previous.get("postings_base")),
            valstr(previous.get("postings_base_directory_hash")),
            deltas,
        )
    } else {
        let base_name = format!("postings.base.{}.bin", generation);
        write_atomic(&directory.join(&base_name), &postings_bytes)?;
        (generation, base_name, postings_hash, Vec::new())
    };
    trace_event(json!({"event":"cache_write","artifact":"postings","bytes":postings_bytes.len()}));
    crash_at("after_postings");

    let compat_bytes = serialize_jsonl(&compat_rows(&sorted_rows))?;
    write_atomic(&index_path(root), &compat_bytes)?;
    trace_event(json!({"event":"compat_index_publish","generation":generation}));
    crash_at("after_compat");
    let manifest = json!({
        "version":2,
        "generation":generation,
        "record_count":sorted_rows.len(),
        "records_file":records_name,
        "records_hash":hash_text(&records_bytes),
        "postings_base_generation":base_generation,
        "postings_base":base_name,
        "postings_base_directory_hash":base_hash,
        "postings_deltas":deltas,
        "compat_hash":hash_text(&compat_bytes),
    });
    let manifest_bytes = serde_json::to_vec(&manifest).map_err(|error| err(error.to_string()))?;
    write_atomic(&manifest_path(root), &manifest_bytes)?;
    trace_event(json!({"event":"cache_publish","generation":generation}));
    crash_at("after_manifest");
    Ok(manifest)
}

fn deduplicate_rows(mut rows: Vec<Value>, tolerate: bool) -> Result<Vec<Value>, ConvError> {
    rows.sort_by(|left, right| valstr(left.get("file")).cmp(&valstr(right.get("file"))));
    let mut ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for row in &rows {
        ids.entry(valstr(row.get("id")))
            .or_default()
            .push(valstr(row.get("file")));
    }
    let duplicates = ids
        .into_iter()
        .filter(|(_, paths)| paths.len() > 1)
        .collect::<Vec<_>>();
    if duplicates.is_empty() {
        return Ok(rows);
    }
    if !tolerate {
        let detail = duplicates
            .iter()
            .map(|(record_id, paths)| format!("{}: {}", record_id, paths.join(", ")))
            .collect::<Vec<_>>()
            .join("; ");
        return Err(err(format!("duplicate relay record id(s): {}", detail)));
    }
    let duplicate_ids = duplicates
        .into_iter()
        .map(|(record_id, _)| record_id)
        .collect::<HashSet<_>>();
    rows.retain(|row| !duplicate_ids.contains(&valstr(row.get("id"))));
    Ok(rows)
}

fn rows_from_parsed(
    root: &Path,
    parsed: Vec<(FileStat, Conv, Vec<u8>)>,
    tolerate: bool,
) -> Result<Vec<Value>, ConvError> {
    let mut rows = Vec::new();
    for (stat, conv, bytes) in parsed {
        rows.push(cache_row(root, &conv, &stat, &bytes)?);
    }
    deduplicate_rows(rows, tolerate)
}

fn fresh_cache(
    root: &Path,
    tolerate: bool,
    full: bool,
    persist: bool,
) -> Result<CacheState, ConvError> {
    ensure(root)?;
    let engine = ScanEngine::configured()?;
    let snapshot = engine.snapshot(root)?;
    let no_cache = env::var("RELAY_NO_CACHE").as_deref() == Ok("1");
    if full || no_cache {
        let rows = rows_from_parsed(
            root,
            engine.parse_files(root, &snapshot, tolerate)?,
            tolerate,
        )?;
        let manifest = if persist {
            Some(publish_cache(
                root,
                &rows,
                load_cache(root).and_then(|cache| cache.manifest).as_ref(),
                false,
            )?)
        } else {
            None
        };
        return Ok(CacheState {
            rows,
            manifest,
            postings_valid: true,
        });
    }
    let loaded = load_cache(root);
    let Some(mut cache) = loaded else {
        let rows = rows_from_parsed(
            root,
            engine.parse_files(root, &snapshot, tolerate)?,
            tolerate,
        )?;
        let hint = manifest_hint(root);
        let manifest = if persist {
            Some(publish_cache(root, &rows, hint.as_ref(), false)?)
        } else {
            None
        };
        return Ok(CacheState {
            rows,
            manifest,
            postings_valid: true,
        });
    };
    let old_rows = cache.rows.clone();
    let mut by_file = cache
        .rows
        .drain(..)
        .map(|row| (valstr(row.get("file")), row))
        .collect::<HashMap<_, _>>();
    let mut rows = Vec::new();
    let mut changed_stats = Vec::new();
    for stat in &snapshot {
        let cached = by_file.remove(&stat.relative);
        let fresh = cached.as_ref().is_some_and(|row| {
            row.get("size").and_then(Value::as_u64) == Some(stat.size)
                && row.get("mtime_ns").and_then(Value::as_u64) == Some(stat.mtime_ns)
                && stat.mtime_ns != 0
                && stat.mtime_ns >= row.get("mtime_ns").and_then(Value::as_u64).unwrap_or(0)
        });
        if fresh {
            rows.push(cached.unwrap());
        } else {
            changed_stats.push(stat.clone());
        }
    }
    let parsed = engine.parse_files(root, &changed_stats, tolerate)?;
    rows.extend(rows_from_parsed(root, parsed, tolerate)?);
    rows = deduplicate_rows(rows, tolerate)?;
    rows.sort_by(|left, right| valstr(left.get("file")).cmp(&valstr(right.get("file"))));
    let rows_changed = serialize_jsonl(&rows)? != serialize_jsonl(&old_rows)?;
    let compat_bytes = serialize_jsonl(&compat_rows(&rows))?;
    let compat_valid = cache
        .manifest
        .as_ref()
        .and_then(|manifest| manifest.get("compat_hash"))
        .and_then(Value::as_str)
        == Some(hash_text(&compat_bytes).as_str())
        && fs::read(index_path(root)).ok().as_deref() == Some(compat_bytes.as_slice());
    if persist && (rows_changed || !cache.postings_valid) {
        cache.manifest = Some(publish_cache(root, &rows, cache.manifest.as_ref(), false)?);
        cache.postings_valid = true;
    } else if persist && !compat_valid {
        write_atomic(&index_path(root), &compat_bytes)?;
        trace_event(
            json!({"event":"compat_index_publish","generation":manifest_generation(cache.manifest.as_ref())}),
        );
    }
    cache.rows = rows;
    Ok(cache)
}

#[derive(Clone, Debug)]
struct RecordWrite {
    relative: String,
    bytes: Vec<u8>,
}

fn journal_path(root: &Path) -> PathBuf {
    root.join(".semble").join("txn.pending")
}

fn encode_journal(writes: &[RecordWrite]) -> Result<Vec<u8>, ConvError> {
    let mut bytes = b"RLYTXN2\0".to_vec();
    bytes.extend((writes.len() as u32).to_le_bytes());
    for write in writes {
        if !normalized_cache_file(&write.relative) {
            return Err(err(format!(
                "invalid transaction record path: {}",
                write.relative
            )));
        }
        let path = write.relative.as_bytes();
        bytes.extend((path.len() as u32).to_le_bytes());
        bytes.extend((write.bytes.len() as u64).to_le_bytes());
        bytes.extend(fnv1a(&write.bytes).to_le_bytes());
        bytes.extend(path);
        bytes.extend(&write.bytes);
    }
    Ok(bytes)
}

fn decode_journal(bytes: &[u8]) -> Result<Vec<RecordWrite>, ConvError> {
    if bytes.len() < 12 || &bytes[..8] != b"RLYTXN2\0" {
        return Err(err("unreadable Relay transaction journal: invalid header"));
    }
    let mut cursor = 8usize;
    let count = u32::from_le_bytes(bytes[cursor..cursor + 4].try_into().unwrap()) as usize;
    cursor += 4;
    if count > 1_000_000 {
        return Err(err(
            "unreadable Relay transaction journal: invalid record count",
        ));
    }
    let mut writes = Vec::with_capacity(count);
    for _ in 0..count {
        if cursor + 20 > bytes.len() {
            return Err(err("unreadable Relay transaction journal: truncated entry"));
        }
        let path_length =
            u32::from_le_bytes(bytes[cursor..cursor + 4].try_into().unwrap()) as usize;
        cursor += 4;
        let data_length =
            u64::from_le_bytes(bytes[cursor..cursor + 8].try_into().unwrap()) as usize;
        cursor += 8;
        let checksum = u64::from_le_bytes(bytes[cursor..cursor + 8].try_into().unwrap());
        cursor += 8;
        let end = cursor
            .checked_add(path_length)
            .and_then(|value| value.checked_add(data_length))
            .ok_or_else(|| err("unreadable Relay transaction journal: length overflow"))?;
        if end > bytes.len() {
            return Err(err("unreadable Relay transaction journal: truncated data"));
        }
        let relative = std::str::from_utf8(&bytes[cursor..cursor + path_length])
            .map_err(|_| err("unreadable Relay transaction journal: path is not UTF-8"))?
            .to_string();
        cursor += path_length;
        let data = bytes[cursor..cursor + data_length].to_vec();
        cursor += data_length;
        if !normalized_cache_file(&relative) || fnv1a(&data) != checksum {
            return Err(err(
                "unreadable Relay transaction journal: invalid path or checksum",
            ));
        }
        writes.push(RecordWrite {
            relative,
            bytes: data,
        });
    }
    if cursor != bytes.len() {
        return Err(err("unreadable Relay transaction journal: trailing bytes"));
    }
    writes.sort_by(|left, right| left.relative.cmp(&right.relative));
    if writes
        .windows(2)
        .any(|pair| pair[0].relative == pair[1].relative)
    {
        return Err(err(
            "unreadable Relay transaction journal: duplicate record path",
        ));
    }
    Ok(writes)
}

fn rows_after_writes(
    root: &Path,
    state: &CacheState,
    writes: &[RecordWrite],
) -> Result<Vec<Value>, ConvError> {
    let dirty = writes
        .iter()
        .map(|write| write.relative.as_str())
        .collect::<HashSet<_>>();
    let mut rows = state
        .rows
        .iter()
        .filter(|row| !dirty.contains(valstr(row.get("file")).as_str()))
        .cloned()
        .collect::<Vec<_>>();
    for write in writes {
        let path = root.join(&write.relative);
        let Ok(conv) = read_conv_bytes(&path, &write.bytes) else {
            continue;
        };
        let metadata = fs::metadata(&path)?;
        let mtime_ns = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
            .unwrap_or(0);
        let stat = FileStat {
            path,
            relative: write.relative.clone(),
            size: metadata.len(),
            mtime_ns,
        };
        rows.push(cache_row(root, &conv, &stat, &write.bytes)?);
    }
    deduplicate_rows(rows, true)
}

fn remove_journal(root: &Path) -> Result<(), ConvError> {
    remove_durable(&journal_path(root)).map_err(ConvError::from)
}

fn commit_writes(
    root: &Path,
    mut writes: Vec<RecordWrite>,
    state: &CacheState,
    incremental: bool,
) -> Result<Vec<Value>, ConvError> {
    writes.sort_by(|left, right| left.relative.cmp(&right.relative));
    writes.dedup_by(|left, right| left.relative == right.relative);
    if writes.is_empty() {
        return Ok(state.rows.clone());
    }
    let journal = encode_journal(&writes)?;
    write_atomic(&journal_path(root), &journal)?;
    trace_event(json!({"event":"journal_publish","bytes":journal.len()}));
    crash_at("after_journal");
    for (index, write) in writes.iter().enumerate() {
        let path = root.join(&write.relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        write_atomic(&path, &write.bytes)?;
        trace_record("record_write", root, &path, 0, write.bytes.len());
        crash_at(&format!("after_record:{}", index + 1));
    }
    let rows = rows_after_writes(root, state, &writes)?;
    publish_cache(root, &rows, state.manifest.as_ref(), incremental)?;
    remove_journal(root)?;
    crash_at("after_journal_unlink");
    Ok(rows)
}

fn recover_journal(root: &Path) -> Result<(), ConvError> {
    let path = journal_path(root);
    if !path.exists() {
        return Ok(());
    }
    let metadata = fs::symlink_metadata(&path)?;
    if is_link_or_reparse(&metadata) || !metadata.is_file() {
        return Err(err(
            "unreadable Relay transaction journal: expected a regular file",
        ));
    }
    let bytes = fs::read(&path)
        .map_err(|error| err(format!("cannot read Relay transaction journal: {}", error)))?;
    let writes = decode_journal(&bytes)?;
    for write in &writes {
        let destination = root.join(&write.relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        write_atomic(&destination, &write.bytes)?;
    }
    let state = fresh_cache(root, true, true, false)?;
    publish_cache(root, &state.rows, manifest_hint(root).as_ref(), false)?;
    remove_journal(root)?;
    Ok(())
}

fn conv_bytes(meta: &BTreeMap<String, Value>, body: &str) -> Result<Vec<u8>, ConvError> {
    Ok(format!("{}{}\n", dump_meta(meta)?, body.trim()).into_bytes())
}

fn relative_record_path(root: &Path, path: &Path) -> Result<String, ConvError> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| err("record path escaped the Plugin installation root"))?
        .to_str()
        .ok_or_else(|| err("record path is not valid UTF-8"))?
        .replace('\\', "/");
    if !normalized_cache_file(&relative) {
        return Err(err("record path is not a normalized Relay archive path"));
    }
    Ok(relative)
}

fn read_row_conv(root: &Path, row: &Value) -> Result<(Conv, Vec<u8>), ConvError> {
    let path = root.join(valstr(row.get("file")));
    let bytes = fs::read(&path)?;
    trace_record("record_open", root, &path, 0, bytes.len());
    let conv = read_conv_bytes(&path, &bytes)?;
    Ok((conv, bytes))
}

fn resolve_row<'a>(state: &'a CacheState, target: &str) -> Result<&'a Value, ConvError> {
    if let Some(row) = state
        .rows
        .iter()
        .find(|row| valstr(row.get("id")) == target)
    {
        return Ok(row);
    }
    let query_terms = terms(target);
    let mut hits = state
        .rows
        .iter()
        .filter(|row| {
            let haystack = serde_json::to_string(&compat_row(row))
                .unwrap_or_default()
                .to_lowercase();
            query_terms.iter().any(|term| haystack.contains(term))
        })
        .collect::<Vec<_>>();
    hits.sort_by(|left, right| valstr(left.get("id")).cmp(&valstr(right.get("id"))));
    match hits.as_slice() {
        [] => Err(err(format!("relay record not found in the Relay archive for {:?}; use list or search to find the relay record id", target))),
        [row] => Ok(*row),
        _ => Err(err(format!(
            "ambiguous target {:?}: {:?}; use list or search with a more specific query",
            target,
            hits.iter().map(|row| valstr(row.get("id"))).collect::<Vec<_>>()
        ))),
    }
}

fn reverse_rel(relation: &str) -> Option<&'static str> {
    match relation {
        "spawned-from" => Some("spawned-to"),
        "continued-from" => Some("continued-as"),
        "informed-by" => Some("informed"),
        _ => None,
    }
}

fn forward_ref_set(
    meta: &BTreeMap<String, Value>,
) -> Result<BTreeSet<(String, String)>, ConvError> {
    Ok(normalize_refs(meta.get("refs"))?
        .into_iter()
        .filter_map(|reference| {
            let relation = valstr(reference.get("rel"));
            reverse_rel(&relation).map(|_| (valstr(reference.get("id")), relation))
        })
        .collect())
}

fn reconcile_writes(
    root: &Path,
    state: &CacheState,
    changes: Vec<(Option<Conv>, Conv, Vec<u8>)>,
) -> Result<(Vec<RecordWrite>, usize), ConvError> {
    let rows_by_id = state
        .rows
        .iter()
        .map(|row| (valstr(row.get("id")), row))
        .collect::<HashMap<_, _>>();
    let mut planned = HashMap::<String, Conv>::new();
    let mut old_by_id = HashMap::<String, Option<Conv>>::new();
    let mut original_ids = Vec::new();
    for (old, new, _) in &changes {
        original_ids.push(id(new));
        old_by_id.insert(id(new), old.clone());
        planned.insert(id(new), new.clone());
    }
    for child_id in &original_ids {
        let old_refs = old_by_id
            .get(child_id)
            .and_then(|value| value.as_ref())
            .map(|conv| forward_ref_set(&conv.meta))
            .transpose()?
            .unwrap_or_default();
        let new_refs = forward_ref_set(&planned[child_id].meta)?;
        let targets = old_refs
            .iter()
            .chain(new_refs.iter())
            .map(|(target, _)| target.clone())
            .collect::<BTreeSet<_>>();
        for target_id in targets {
            let mut target = if let Some(conv) = planned.get(&target_id) {
                conv.clone()
            } else if let Some(row) = rows_by_id.get(&target_id) {
                read_row_conv(root, row)?.0
            } else {
                continue;
            };
            let mut refs = normalize_refs(target.meta.get("refs"))?;
            let before = refs.clone();
            for (_, relation) in old_refs.iter().filter(|(target, _)| target == &target_id) {
                let reverse = reverse_rel(relation).unwrap();
                refs.retain(|reference| {
                    !(valstr(reference.get("id")) == *child_id
                        && valstr(reference.get("rel")) == reverse)
                });
            }
            for (_, relation) in new_refs.iter().filter(|(target, _)| target == &target_id) {
                let reverse = reverse_rel(relation).unwrap();
                refs.push(json!({"id":child_id,"rel":reverse}));
            }
            refs = normalize_refs(Some(&Value::Array(refs)))?;
            if refs != before {
                target.meta.insert("refs".into(), Value::Array(refs));
                target.meta.insert("updated".into(), json!(now_utc()));
                planned.insert(target_id, target);
            }
        }
    }
    let mut writes = Vec::new();
    for (_, new, old_bytes) in changes {
        let bytes = conv_bytes(&planned[&id(&new)].meta, &planned[&id(&new)].body)?;
        if bytes != old_bytes {
            writes.push(RecordWrite {
                relative: relative_record_path(root, &new.path)?,
                bytes,
            });
        }
    }
    let mut neighbor_changes = 0usize;
    for (record_id, conv) in planned {
        if original_ids.contains(&record_id) {
            continue;
        }
        let bytes = conv_bytes(&conv.meta, &conv.body)?;
        writes.push(RecordWrite {
            relative: relative_record_path(root, &conv.path)?,
            bytes,
        });
        neighbor_changes += 1;
    }
    Ok((writes, neighbor_changes))
}
fn rebuild(root: &Path, tolerate: bool) -> Result<Vec<Value>, ConvError> {
    rebuild_mode(root, tolerate, false)
}
fn rebuild_mode(root: &Path, tolerate: bool, full: bool) -> Result<Vec<Value>, ConvError> {
    Ok(compat_rows(&fresh_cache(root, tolerate, full, true)?.rows))
}
fn read_index(root: &Path, tolerate: bool) -> Result<Vec<Value>, ConvError> {
    Ok(compat_rows(&fresh_cache(root, tolerate, false, true)?.rows))
}
fn valid_index(root: &Path, v: &Value) -> bool {
    let Some(o) = v.as_object() else { return false };
    for k in [
        "id", "topic", "status", "tags", "refs", "created", "updated", "file", "open",
    ] {
        if !o.contains_key(k) {
            return false;
        }
    }
    let Some(f) = o.get("file").and_then(|x| x.as_str()) else {
        return false;
    };
    if !f.starts_with("convs/") || f.contains("..") || f.contains('\\') || !f.ends_with(".md") {
        return false;
    }
    root.join(f).is_file()
}
fn upsert(
    root: &Path,
    raw: Map<String, Value>,
    override_status: Option<&str>,
    create_only: bool,
) -> Result<Value, ConvError> {
    ensure(root)?;
    let state = fresh_cache(root, false, false, true)?;
    let rid = raw.get("id").map(|v| valstr(Some(v))).unwrap_or_default();
    let existing_row = (!rid.is_empty())
        .then(|| state.rows.iter().find(|row| valstr(row.get("id")) == rid))
        .flatten();
    let (existing, existing_bytes) = match existing_row {
        Some(row) => {
            let (conv, bytes) = read_row_conv(root, row)?;
            (Some(conv), bytes)
        }
        None => (None, Vec::new()),
    };
    if create_only && existing.is_some() {
        return Err(err(format!("conversation already exists: {}", rid)));
    }
    let mut raw2 = raw.clone();
    if let Some(s) = override_status {
        raw2.insert("status".into(), json!(s));
    }
    let known_ids = state
        .rows
        .iter()
        .map(|row| valstr(row.get("id")))
        .collect::<HashSet<_>>();
    let meta = normalize_meta(
        &raw2,
        existing.as_ref().map(|c| &c.meta),
        root,
        Some(&known_ids),
    )?;
    let body = build_body(&raw2)?;
    let path = existing
        .as_ref()
        .map(|c| c.path.clone())
        .unwrap_or(path_for(root, &id_from(&meta))?);
    let conv = Conv {
        path: path.clone(),
        meta: meta.clone(),
        body: body.clone(),
    };
    let (writes, ref_changes) =
        reconcile_writes(root, &state, vec![(existing, conv, existing_bytes)])?;
    let relative = relative_record_path(root, &path)?;
    let changed = writes.iter().any(|write| write.relative == relative);
    let records = commit_writes(root, writes, &state, true)?;
    Ok(
        json!({"id":id_from(&meta),"file":relative,"changed":changed,"ref_changes":ref_changes,"index_records":records.len()}),
    )
}
fn id_from(m: &BTreeMap<String, Value>) -> String {
    valstr(m.get("id"))
}
fn regen(root: &Path) -> Result<(usize, usize), ConvError> {
    regen_mode(root, false)
}
fn regen_mode(root: &Path, tolerate: bool) -> Result<(usize, usize), ConvError> {
    ensure(root)?;
    let previous = load_cache(root)
        .and_then(|cache| cache.manifest)
        .or_else(|| manifest_hint(root));
    let engine = ScanEngine::configured()?;
    let snapshot = engine.snapshot(root)?;
    let parsed = engine.parse_files(root, &snapshot, tolerate)?;
    let mut cs = Vec::new();
    let mut rows = Vec::new();
    for (stat, conv, bytes) in parsed {
        rows.push(cache_row(root, &conv, &stat, &bytes)?);
        cs.push((conv, bytes));
    }
    rows = deduplicate_rows(rows, tolerate)?;
    let state = CacheState {
        rows,
        manifest: previous,
        postings_valid: true,
    };
    let ids: HashSet<_> = cs.iter().map(|(conv, _)| id(conv)).collect();
    let mut desired: HashMap<String, HashSet<(String, String)>> = HashMap::new();
    for (c, _) in &cs {
        let set = normalize_refs(c.meta.get("refs"))?
            .into_iter()
            .filter_map(|r| {
                let rel = valstr(r.get("rel"));
                if ["spawned-from", "continued-from", "informed-by"].contains(&rel.as_str()) {
                    Some((valstr(r.get("id")), rel))
                } else {
                    None
                }
            })
            .collect();
        desired.insert(id(c), set);
    }
    for (c, _) in &cs {
        for (t, rel) in desired[&id(c)].clone() {
            if !ids.contains(&t) {
                continue;
            }
            let rev = match rel.as_str() {
                "spawned-from" => "spawned-to",
                "continued-from" => "continued-as",
                "informed-by" => "informed",
                _ => continue,
            };
            desired.get_mut(&t).unwrap().insert((id(c), rev.into()));
        }
    }
    let mut writes = Vec::new();
    for (c, _old_bytes) in cs {
        let old: HashSet<_> = normalize_refs(c.meta.get("refs"))?
            .into_iter()
            .map(|r| (valstr(r.get("id")), valstr(r.get("rel"))))
            .collect();
        if old != desired[&id(&c)] {
            let mut m = c.meta.clone();
            m.insert(
                "refs".into(),
                json!(desired[&id(&c)]
                    .iter()
                    .map(|(i, r)| json!({"id":i,"rel":r}))
                    .collect::<Vec<_>>()),
            );
            m.insert("updated".into(), json!(now_utc()));
            writes.push(RecordWrite {
                relative: relative_record_path(root, &c.path)?,
                bytes: conv_bytes(&m, &c.body)?,
            });
        }
    }
    let changed = writes.len();
    let records = if writes.is_empty() {
        publish_cache(root, &state.rows, state.manifest.as_ref(), false)?;
        state.rows.len()
    } else {
        commit_writes(root, writes, &state, false)?.len()
    };
    Ok((changed, records))
}
fn resolve(root: &Path, target: &str) -> Result<Conv, ConvError> {
    if let Some(c) = find(root, target)? {
        return Ok(c);
    };
    let hits = search(root, target, 5, false)?;
    if hits.is_empty() {
        return Err(err(format!("relay record not found in the Relay archive for {:?}; use list or search to find the relay record id",target)));
    };
    if hits.len() != 1 {
        return Err(err(format!(
            "ambiguous target {:?}: {:?}; use list or search with a more specific query",
            target,
            hits.iter().map(|v| valstr(v.get("id"))).collect::<Vec<_>>()
        )));
    };
    find(root, &valstr(hits[0].get("id")))?
        .ok_or_else(|| err("conversation not found after search"))
}
fn terms(q: &str) -> Vec<String> {
    let stop = [
        "a",
        "an",
        "and",
        "conv",
        "conversation",
        "discussion",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "where",
        "we",
        "with",
    ];
    q.to_lowercase()
        .split(|character: char| !character.is_ascii_alphanumeric())
        .filter(|term| !term.is_empty())
        .map(str::to_string)
        .filter(|s| !stop.contains(&s.as_str()))
        .collect()
}
fn posting_file(root: &Path, manifest: &Value) -> Option<(PathBuf, String)> {
    let directory = index_v2(root);
    if let Some(delta) = manifest
        .get("postings_deltas")
        .and_then(Value::as_array)
        .and_then(|values| values.last())
    {
        let name = delta.get("file")?.as_str()?;
        let hash = delta.get("directory_hash")?.as_str()?.to_string();
        return safe_named_artifact(&directory, name).map(|path| (path, hash));
    }
    let name = manifest.get("postings_base")?.as_str()?;
    let hash = manifest
        .get("postings_base_directory_hash")?
        .as_str()?
        .to_string();
    safe_named_artifact(&directory, name).map(|path| (path, hash))
}

fn posting_candidates(
    root: &Path,
    cache: &CacheState,
    tier: u8,
    query_terms: &[String],
) -> Result<Option<HashSet<String>>, ConvError> {
    if env::var("RELAY_NO_CACHE").as_deref() == Ok("1")
        || query_terms
            .iter()
            .any(|term| term.len() < 3 || !term.is_ascii())
    {
        return Ok(None);
    }
    let manifest = cache
        .manifest
        .as_ref()
        .ok_or_else(|| err("postings manifest unavailable"))?;
    let (path, directory_hash) =
        posting_file(root, manifest).ok_or_else(|| err("postings artifact unavailable"))?;
    let (directory, blocks_offset) = read_posting_directory(&path, &directory_hash, true)?;
    let entries = directory
        .iter()
        .filter_map(|entry| {
            let object = entry.as_object()?;
            Some((
                (
                    object.get("t")?.as_u64()? as u8,
                    object.get("g")?.as_str()?.to_string(),
                ),
                entry,
            ))
        })
        .collect::<HashMap<_, _>>();
    let mut union = HashSet::new();
    let mut file = fs::File::open(path)?;
    for term in query_terms {
        let mut intersection: Option<HashSet<String>> = None;
        for gram in term.as_bytes().windows(3) {
            let key = (tier, hex_gram(gram));
            let Some(entry) = entries.get(&key) else {
                intersection = Some(HashSet::new());
                break;
            };
            let offset = entry
                .get("o")
                .and_then(Value::as_u64)
                .ok_or_else(|| err("invalid postings offset"))?;
            let length = entry
                .get("l")
                .and_then(Value::as_u64)
                .ok_or_else(|| err("invalid postings length"))?;
            if length > 64 * 1024 * 1024 {
                return Err(err("postings block is too large"));
            }
            file.seek(SeekFrom::Start(blocks_offset + offset))?;
            let mut block = vec![0u8; length as usize];
            file.read_exact(&mut block)?;
            trace_event(
                json!({"event":"cache_read","artifact":"postings_block","bytes":block.len()}),
            );
            if hash_text(&block) != entry.get("h").and_then(Value::as_str).unwrap_or("") {
                return Err(err("postings block checksum mismatch"));
            }
            let ids = std::str::from_utf8(&block)
                .map_err(|_| err("postings block is not UTF-8"))?
                .lines()
                .map(str::to_string)
                .collect::<HashSet<_>>();
            intersection = Some(match intersection {
                None => ids,
                Some(current) => current.intersection(&ids).cloned().collect(),
            });
        }
        union.extend(intersection.unwrap_or_default());
    }
    Ok(Some(union))
}

fn ranked_tier(
    rows: &[Value],
    query_terms: &[String],
    tier: u8,
    candidates: Option<&HashSet<String>>,
) -> Vec<Value> {
    let mut hits = rows
        .iter()
        .filter(|row| candidates.is_none_or(|ids| ids.contains(&valstr(row.get("id")))))
        .filter_map(|row| {
            let haystack = if tier == 1 {
                format!("{} {}", valstr(row.get("id")), valstr(row.get("file")))
            } else {
                serde_json::to_string(row).unwrap_or_default()
            };
            let lower = haystack.to_lowercase();
            let score = query_terms
                .iter()
                .filter(|term| lower.contains(term.as_str()))
                .count() as i64;
            (score > 0).then(|| {
                let mut object = row.as_object().cloned().unwrap_or_default();
                object.insert(
                    "layer".into(),
                    json!(if tier == 1 {
                        "fff"
                    } else {
                        "rg-index-fallback"
                    }),
                );
                object.insert("score".into(), json!(score));
                Value::Object(object)
            })
        })
        .collect::<Vec<_>>();
    hits.sort_by(|left, right| {
        right
            .get("score")
            .and_then(Value::as_i64)
            .cmp(&left.get("score").and_then(Value::as_i64))
            .then_with(|| valstr(right.get("updated")).cmp(&valstr(left.get("updated"))))
            .then_with(|| valstr(left.get("id")).cmp(&valstr(right.get("id"))))
    });
    hits
}

fn search(root: &Path, q: &str, limit: usize, no_semble: bool) -> Result<Vec<Value>, ConvError> {
    if limit == usize::MAX {
        return Err(err("--limit must be >= 0"));
    };
    if limit == 0 {
        return Ok(Vec::new());
    }
    if !no_semble {
        if let Ok(value) = env::var("RELAY_SEMBLE_TIMEOUT") {
            let valid = value
                .parse::<f64>()
                .ok()
                .is_some_and(|seconds| seconds.is_finite() && seconds > 0.0);
            if !valid {
                return Err(err(
                    "RELAY_SEMBLE_TIMEOUT must be a positive finite duration in seconds",
                ));
            }
        }
    }
    let mut cache = fresh_cache(root, true, false, true)?;
    let rec = compat_rows(&cache.rows);
    let mut ts = terms(q);
    if ts.is_empty() && !q.trim().is_empty() {
        ts.push(q.to_lowercase().trim().into())
    };
    if let Some(exact) = rec.iter().find(|row| valstr(row.get("id")) == q.trim()) {
        let mut object = exact.as_object().cloned().unwrap_or_default();
        object.insert("layer".into(), json!("fff"));
        object.insert("score".into(), json!(ts.len().max(1)));
        return Ok(vec![Value::Object(object)]);
    }
    let tier_one_candidates = posting_candidates(root, &cache, 1, &ts).unwrap_or(None);
    let mut hits = ranked_tier(&rec, &ts, 1, tier_one_candidates.as_ref());
    if !hits.is_empty() {
        if ts.len() == 1 {
            hits.truncate(1);
        }
        hits.truncate(limit);
        return Ok(hits);
    };
    let tier_two_candidates = match posting_candidates(root, &cache, 2, &ts) {
        Ok(candidates) => candidates,
        Err(_) => {
            cache.manifest = Some(publish_cache(
                root,
                &cache.rows,
                cache.manifest.as_ref(),
                false,
            )?);
            None
        }
    };
    let mut ih = ranked_tier(&rec, &ts, 2, tier_two_candidates.as_ref());
    if !ih.is_empty() {
        if ts.len() == 1 {
            ih.truncate(1);
        }
        ih.truncate(limit);
        return Ok(ih);
    };
    if !no_semble {
        let semble_hits = search_semble(root, &rec, q, limit);
        if !semble_hits.is_empty() {
            return Ok(semble_hits);
        }
    }

    let stats = cache
        .rows
        .iter()
        .map(|row| FileStat {
            relative: valstr(row.get("file")),
            path: root.join(valstr(row.get("file"))),
            size: row.get("size").and_then(Value::as_u64).unwrap_or(0),
            mtime_ns: row.get("mtime_ns").and_then(Value::as_u64).unwrap_or(0),
        })
        .collect::<Vec<_>>();
    let engine = ScanEngine::configured()?;
    let mut bh = Vec::new();
    for (_, c, _) in engine.parse_files(root, &stats, true)? {
        let lower = c.body.to_lowercase();
        let s = ts
            .iter()
            .filter(|term| lower.contains(term.as_str()))
            .count() as i64;
        if s > 0 {
            let mut o = index_record(root, &c)?.as_object().unwrap().clone();
            o.insert("layer".into(), json!("semble-body-fallback"));
            o.insert("score".into(), json!(s));
            bh.push(Value::Object(o))
        }
    }
    bh.truncate(limit);
    Ok(bh)
}

#[derive(Clone, Debug)]
struct TranscriptExchange {
    markdown: String,
    weight: u8,
    age: usize,
}

fn marker_weight(line: &str) -> Option<u8> {
    line.strip_prefix("<!-- relay:transcript-weight=")
        .and_then(|value| value.strip_suffix(" -->"))
        .and_then(|value| value.parse::<u8>().ok())
        .filter(|value| (1..=3).contains(value))
}

fn transcript_exchanges(markdown: &str, schema_two: bool) -> Vec<TranscriptExchange> {
    let lines = markdown.lines().collect::<Vec<_>>();
    let mut exchanges = Vec::new();
    let mut index = 0usize;
    while index < lines.len() {
        if lines[index].trim().is_empty() || lines[index].trim() == NONE {
            index += 1;
            continue;
        }
        let mut weight = 1u8;
        let mut begin = index;
        if schema_two {
            if let Some(value) = marker_weight(lines[index]) {
                if lines
                    .get(index + 1)
                    .is_some_and(|line| line.starts_with("- U:") || line.starts_with("- A:"))
                {
                    weight = value;
                    index += 1;
                    begin = index;
                }
            }
        }
        if lines[index].starts_with("- U:") || lines[index].starts_with("- A:") {
            let first_kind = if lines[index].starts_with("- U:") {
                'U'
            } else {
                'A'
            };
            index += 1;
            while index < lines.len() {
                let line = lines[index];
                if marker_weight(line).is_some() || line.starts_with("- U:") {
                    break;
                }
                if line.starts_with("- A:") && first_kind == 'A' {
                    break;
                }
                if line.starts_with("- ") && !line.starts_with("- A:") {
                    break;
                }
                index += 1;
            }
            exchanges.push(TranscriptExchange {
                markdown: lines[begin..index].join("\n"),
                weight,
                age: exchanges.len(),
            });
        } else {
            index += 1;
            exchanges.push(TranscriptExchange {
                markdown: lines[begin..index].join("\n"),
                weight: 1,
                age: exchanges.len(),
            });
        }
    }
    exchanges
}

#[derive(Clone, Debug)]
struct LinkedUnit {
    id: String,
    rel: String,
    topic: Option<String>,
    status: Option<String>,
    digest: Option<String>,
    error: Option<String>,
}

fn context_frontmatter(conv: &Conv) -> Result<Value, ConvError> {
    Ok(json!({
        "id":id(conv),
        "topic":valstr(conv.meta.get("topic")),
        "status":valstr(conv.meta.get("status")),
        "tags":conv.meta.get("tags").cloned().unwrap_or_else(|| json!([])),
        "refs":normalize_refs(conv.meta.get("refs"))?,
    }))
}

fn context_text(
    frontmatter: &Value,
    sections: &[(String, String)],
    links: &[LinkedUnit],
    action: &[String],
    truncated: bool,
) -> String {
    let mut parts = vec![
        "relay context pack v2".to_string(),
        format!(
            "frontmatter: {}",
            serde_json::to_string(frontmatter).unwrap_or_default()
        ),
    ];
    for (name, markdown) in sections {
        parts.push(format!("## {}\n{}", name, markdown.trim()));
    }
    if !links.is_empty() {
        let mut rendered = Vec::new();
        for link in links {
            if let Some(error) = &link.error {
                rendered.push(format!("- id={} rel={} error={}", link.id, link.rel, error));
            } else {
                let mut line = format!(
                    "- id={} rel={} topic={} status={}",
                    link.id,
                    link.rel,
                    link.topic.as_deref().unwrap_or(""),
                    link.status.as_deref().unwrap_or("")
                );
                if let Some(digest) = &link.digest {
                    line.push_str(&format!("\n  digest: {}", digest));
                }
                rendered.push(line);
            }
        }
        parts.push(format!("## linked-context\n{}", rendered.join("\n")));
    }
    parts.push(format!(
        "next action argv: {}",
        serde_json::to_string(action).unwrap_or_default()
    ));
    parts.push(format!(
        "truncated: {}",
        if truncated { "yes" } else { "no" }
    ));
    format!("{}\n", parts.join("\n\n"))
}

fn estimated_tokens(text: &str) -> usize {
    text.len().div_ceil(4)
}

fn context_estimated_tokens(text: &str, root: &Path) -> usize {
    let root_text = root.to_string_lossy();
    let escaped = serde_json::to_string(root_text.as_ref()).unwrap_or_default();
    let escaped = escaped
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .unwrap_or(&escaped);
    estimated_tokens(&text.replace(escaped, "<relay-root>"))
}

fn parse_budget(args: &[String]) -> Result<Option<usize>, ConvError> {
    let Some(index) = args.iter().position(|arg| arg == "--budget-tokens") else {
        return Ok(None);
    };
    let value = args
        .get(index + 1)
        .ok_or_else(|| err("argument --budget-tokens: expected one argument"))?;
    let parsed = value
        .parse::<usize>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| err("--budget-tokens must be an integer greater than 0"))?;
    Ok(Some(parsed))
}

fn cmd_context(root: &Path, args: &[String]) -> Result<(), ConvError> {
    let target = args.first().ok_or_else(|| err("missing target"))?;
    let budget = parse_budget(args)?;
    let as_json = args.iter().any(|arg| arg == "--json");
    let no_refs = args.iter().any(|arg| arg == "--no-refs");
    let owner = resolve(root, target)?;
    let section_values = sections_allow_dup(&owner.body)
        .map_err(|error| err(format!("{}: {}", id(&owner), error)))?;
    let frontmatter = context_frontmatter(&owner)?;
    let action = vec![
        env::current_exe()?.to_string_lossy().to_string(),
        "set-status".into(),
        id(&owner),
        "active".into(),
        "--relay-root".into(),
        root.to_string_lossy().to_string(),
    ];
    let mandatory_names = ["summary", "glossary", "user-instructions", "resume", "qa"];
    let optional_names = ["decisions", "environment", "artifacts", "sources", "insights"];
    let mandatory = mandatory_names
        .iter()
        .map(|name| {
            (
                (*name).to_string(),
                section_values
                    .get(*name)
                    .cloned()
                    .unwrap_or_else(|| NONE.into()),
            )
        })
        .collect::<Vec<_>>();
    let mut optional = optional_names
        .iter()
        .filter_map(|name| {
            section_values
                .get(*name)
                .filter(|value| !value.is_empty())
                .map(|value| ((*name).to_string(), value.clone()))
        })
        .collect::<Vec<_>>();
    let transcript = section_values
        .get("condensed-transcript")
        .cloned()
        .unwrap_or_default();
    let mut exchanges = transcript_exchanges(
        &transcript,
        owner.meta.get("relay_schema").and_then(Value::as_u64) == Some(2),
    );
    let mut links = Vec::new();
    if !no_refs {
        let state = fresh_cache(root, true, false, true)?;
        let by_id = state
            .rows
            .iter()
            .map(|row| (valstr(row.get("id")), row))
            .collect::<HashMap<_, _>>();
        for reference in normalize_refs(owner.meta.get("refs"))? {
            let linked_id = valstr(reference.get("id"));
            let rel = valstr(reference.get("rel"));
            if let Some(row) = by_id.get(&linked_id) {
                match read_row_conv(root, row) {
                    Ok((linked, _)) => {
                        let linked_sections = sections_allow_dup(&linked.body)
                            .map_err(|error| err(format!("{}: {}", linked_id, error)))?;
                        let status = valstr(linked.meta.get("status"));
                        links.push(LinkedUnit {
                            id: linked_id,
                            rel,
                            topic: Some(valstr(linked.meta.get("topic"))),
                            digest: (status == "closed")
                                .then(|| linked_sections.get("digest").cloned())
                                .flatten(),
                            status: Some(status),
                            error: None,
                        });
                    }
                    Err(_) => links.push(LinkedUnit {
                        id: linked_id,
                        rel,
                        topic: None,
                        status: None,
                        digest: None,
                        error: Some("malformed".into()),
                    }),
                }
            } else {
                links.push(LinkedUnit {
                    id: linked_id,
                    rel,
                    topic: None,
                    status: None,
                    digest: None,
                    error: Some("missing".into()),
                });
            }
        }
        links.sort_by(|left, right| {
            (&left.id, &left.rel, &left.error).cmp(&(&right.id, &right.rel, &right.error))
        });
    }

    let render_sections = |mandatory: &[(String, String)],
                           optional: &[(String, String)],
                           exchanges: &[TranscriptExchange]| {
        let mut values = mandatory.to_vec();
        values.extend(optional.iter().cloned());
        if !exchanges.is_empty() {
            values.push((
                "condensed-transcript".into(),
                exchanges
                    .iter()
                    .map(|exchange| exchange.markdown.as_str())
                    .collect::<Vec<_>>()
                    .join("\n"),
            ));
        }
        values
    };
    let minimum_text = context_text(&frontmatter, &mandatory, &[], &action, true);
    let minimum = context_estimated_tokens(&minimum_text, root);
    if let Some(limit) = budget {
        if limit < minimum {
            return Err(err(format!(
                "context budget is too small; minimum required estimate is {} tokens",
                minimum
            )));
        }
    }
    let mut truncated = false;
    if let Some(limit) = budget {
        while {
            let candidate = context_text(
                &frontmatter,
                &render_sections(&mandatory, &optional, &exchanges),
                &links,
                &action,
                truncated,
            );
            estimated_tokens(&candidate) > limit
                || context_estimated_tokens(&candidate, root) > limit
        } {
            truncated = true;
            if !links.is_empty() {
                links.pop();
                continue;
            }
            if !exchanges.is_empty() {
                if exchanges.iter().all(|exchange| exchange.weight == 3) {
                    if let Some(name) = ["insights", "sources", "artifacts", "environment", "decisions"]
                        .iter()
                        .find(|name| optional.iter().any(|(section, _)| section == **name))
                    {
                        let index = optional
                            .iter()
                            .position(|(section, _)| section == *name)
                            .unwrap();
                        optional.remove(index);
                        continue;
                    }
                }
                let drop_index = exchanges
                    .iter()
                    .enumerate()
                    .min_by_key(|(_, exchange)| (exchange.weight, exchange.age))
                    .map(|(index, _)| index)
                    .unwrap();
                exchanges.remove(drop_index);
                continue;
            }
            if let Some(name) = ["insights", "sources", "artifacts", "environment", "decisions"]
                .iter()
                .find(|name| optional.iter().any(|(section, _)| section == **name))
            {
                let index = optional
                    .iter()
                    .position(|(section, _)| section == *name)
                    .unwrap();
                optional.remove(index);
                continue;
            }
            return Err(err(format!(
                "context budget is too small; minimum required estimate is {} tokens",
                minimum
            )));
        }
    }
    let selected_sections = render_sections(&mandatory, &optional, &exchanges);
    let text = context_text(&frontmatter, &selected_sections, &links, &action, truncated);
    let estimate = context_estimated_tokens(&text, root);
    if as_json {
        let linked = links
            .iter()
            .filter(|link| link.error.is_none())
            .map(|link| {
                let mut value = json!({
                    "id":link.id,"rel":link.rel,"topic":link.topic,"status":link.status,
                });
                if let Some(digest) = &link.digest {
                    value["digest"] = json!(digest);
                }
                value
            })
            .collect::<Vec<_>>();
        let warnings = links
            .iter()
            .filter_map(|link| {
                link.error
                    .as_ref()
                    .map(|error| json!({"id":link.id,"rel":link.rel,"error":error}))
            })
            .collect::<Vec<_>>();
        output(&json!({
            "schema_version":2,
            "id":id(&owner),
            "plugin_installation_root":root,
            "budget_tokens":budget,
            "estimated_tokens":estimate,
            "minimum_tokens":minimum,
            "truncated":truncated,
            "frontmatter":frontmatter,
            "sections":selected_sections.into_iter().map(|(name,markdown)| json!({"name":name,"markdown":markdown})).collect::<Vec<_>>(),
            "linked":linked,
            "warnings":warnings,
            "action_argv":action,
        }));
    } else {
        print!("{}", text);
    }
    Ok(())
}
fn output(v: &Value) {
    println!("{}", serde_json::to_string_pretty(v).unwrap())
}
fn print_help(command: &str) {
    const COMMANDS: &[(&str, &str)] = &[
        (
            "init",
            "create the Relay installation root and Relay archive",
        ),
        (
            "rebuild-index",
            "rebuild index.jsonl from the Relay archive",
        ),
        ("regen-refs", "reconcile bidirectional conversation refs"),
        ("upsert", "create or replace a distilled Relay record"),
        ("set-status", "set a conversation status"),
        ("sidekick", "create an active sidekick branch"),
        (
            "continue",
            "park a conversation and continue it in a fresh record",
        ),
        ("return", "close a branch with a digest"),
        ("list", "list Relay records from the index"),
        ("search", "search Relay records"),
        ("show", "show one Relay record"),
        ("context", "emit a budget-aware Relay context pack"),
        (
            "import",
            "copy missing records from an explicit legacy archive",
        ),
        ("doctor", "validate layout and repair with --fix"),
        ("hook", "process a Codex or Claude UserPromptSubmit hook"),
    ];
    println!("Relay session handoff helper");
    println!("Plugin installation root (Relay installation root) defaults to ~/.relay; the Relay archive is under the root.");
    if command == "--help" {
        println!("\nUsage: relay [--relay-root PATH] <COMMAND> [OPTIONS]\n\nCommands:");
        for (name, description) in COMMANDS {
            println!("  {name:<14}{description}");
        }
        return;
    }
    if let Some((_, description)) = COMMANDS.iter().find(|(name, _)| *name == command) {
        println!("\nUsage: relay [--relay-root PATH] {command} [OPTIONS]\n\n{description}");
    }
}

fn parse_opts() -> Result<(Option<String>, String, Vec<String>), ConvError> {
    let mut a: Vec<String> = env::args().skip(1).collect();
    let mut root = None;
    let mut i = 0;
    while i < a.len() {
        if a[i] == "--relay-root" || a[i] == "--conv-root" {
            let flag = a[i].clone();
            if i + 1 >= a.len() {
                return Err(err(format!("argument {flag}: expected one argument")));
            };
            root = Some(a.remove(i + 1));
            a.remove(i);
        } else {
            i += 1
        }
    }
    if a.is_empty() {
        return Err(err("the following arguments are required: cmd"));
    };
    let cmd = a.remove(0);
    Ok((root, cmd, a))
}
fn hook(args: &[String]) {
    hook_runtime::run(args);
}
fn main() {
    let (rootarg, cmd, args) = match parse_opts() {
        Ok(x) => x,
        Err(e) => {
            eprintln!("relay: {}", e);
            process::exit(2)
        }
    };
    if args.iter().any(|x| x == "--help") || cmd == "--help" {
        print_help(&cmd);
        return;
    }
    if cmd == "hook" {
        hook(&args);
        return;
    }
    let root = match root_from(rootarg.as_ref()) {
        Ok(root) => root,
        Err(error) => {
            eprintln!("relay: {}", error);
            process::exit(2)
        }
    };
    let result = dispatch(&root, &cmd, &args, rootarg.is_some());
    match result {
        Ok(()) => {}
        Err(e) => {
            eprintln!("relay: {}", e);
            process::exit(2)
        }
    }
}
fn dispatch(root: &Path, cmd: &str, args: &[String], compat: bool) -> Result<(), ConvError> {
    const COMMANDS: &[&str] = &[
        "init",
        "rebuild-index",
        "regen-refs",
        "upsert",
        "set-status",
        "sidekick",
        "continue",
        "return",
        "import",
        "list",
        "search",
        "show",
        "context",
        "doctor",
    ];
    if !COMMANDS.contains(&cmd) {
        return Err(err(format!("invalid choice: {}", cmd)));
    }
    let mutates_store = matches!(
        cmd,
        "init"
            | "rebuild-index"
            | "regen-refs"
            | "upsert"
            | "set-status"
            | "sidekick"
            | "continue"
            | "return"
            | "import"
    ) || (cmd == "doctor" && args.iter().any(|arg| arg == "--fix"));
    let mut exclusive = None;
    let mut shared = None;
    if mutates_store {
        exclusive = Some(mutation_lock(root)?);
        recover_journal(root)?;
    } else {
        shared = read_lock_with_recovery(root)?;
    }

    match cmd {
        "init" => {
            ensure(root)?;
            write_gitignore(root)?;
            let r = rebuild(root, false)?;
            let relay_archive = convs(root);
            output(
                &json!({"plugin_installation_root":root,"relay_archive":relay_archive,"conversation_database":relay_archive,"deprecated":{"aliases":{"conv_root":root,"convs":relay_archive,"conversation_database":relay_archive}},"index":index_path(root),"records":r.len()}),
            );
        }
        "rebuild-index" => {
            output(
                &json!({"records":rebuild_mode(root,false,args.iter().any(|arg| arg == "--full"))?.len()}),
            );
        }
        "regen-refs" => {
            let (c, n) = regen(root)?;
            output(&json!({"ref_changes":c,"records":n}));
        }
        "upsert" => cmd_upsert(root, args)?,
        "set-status" => cmd_set_status(root, args)?,
        "sidekick" => cmd_branch(root, args, true)?,
        "continue" => cmd_branch(root, args, false)?,
        "return" => cmd_return(root, args)?,
        "import" => cmd_import(root, args)?,
        "list" => cmd_list(root, args)?,
        "search" => {
            if args.is_empty() {
                return Err(err("missing query"));
            };
            let lim = opt_limit(args)?;
            output(&Value::Array(search(
                root,
                &args[0],
                lim,
                args.iter().any(|arg| arg == "--no-semble"),
            )?));
        }
        "show" => {
            if args.is_empty() {
                return Err(err("missing target"));
            };
            let c = resolve(root, &args[0])?;
            if args.iter().any(|x| x == "--markdown") {
                print!("{}", fs::read_to_string(c.path)?)
            } else {
                let mut o = index_record(root, &c)?.as_object().unwrap().clone();
                o.insert("body".into(), json!(c.body));
                output(&Value::Object(o));
            }
        }
        "context" => cmd_context(root, args)?,
        "doctor" => cmd_doctor(root, args, compat)?,
        _ => unreachable!(),
    }
    if exclusive.take().is_some() {
        trace_event(json!({"event":"lock_release","mode":"exclusive"}));
    }
    if shared.take().is_some() {
        trace_event(json!({"event":"lock_release","mode":"shared"}));
    }
    Ok(())
}
fn opt_limit(args: &[String]) -> Result<usize, ConvError> {
    let mut x = 10i64;
    for i in 0..args.len() {
        if args[i] == "--limit" {
            if i + 1 >= args.len() {
                return Err(err("argument --limit: expected one argument"));
            };
            x = args[i + 1].parse().unwrap_or(-1)
        }
    }
    if x < 0 {
        return Err(err("--limit must be >= 0"));
    }
    Ok(x as usize)
}
fn cmd_upsert(root: &Path, args: &[String]) -> Result<(), ConvError> {
    let mut stdin = false;
    let mut file = None;
    let mut st = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--stdin" => stdin = true,
            "--json" => {
                i += 1;
                if i < args.len() {
                    file = Some(args[i].clone())
                }
            }
            "--status" => {
                i += 1;
                if i < args.len() {
                    st = Some(args[i].clone())
                }
            }
            _ => {}
        }
        i += 1
    }
    let text = if stdin {
        let mut s = String::new();
        io::stdin().read_to_string(&mut s)?;
        s
    } else if let Some(f) = file {
        fs::read_to_string(&f).map_err(|e| err(format!("cannot read JSON from {}: {}", f, e)))?
    } else {
        return Err(err("upsert requires --stdin or --json PATH"));
    };
    let v: Value =
        serde_json::from_str(&text).map_err(|e| err(format!("stdin has invalid JSON: {}", e)))?;
    let o = v
        .as_object()
        .ok_or_else(|| err("stdin must contain a JSON object"))?
        .clone();
    output(&upsert(root, o, st.as_deref(), false)?);
    Ok(())
}
fn cmd_set_status(root: &Path, args: &[String]) -> Result<(), ConvError> {
    if args.len() < 2 {
        return Err(err("the following arguments are required: id, status"));
    };
    let cid = &args[0];
    let st = &args[1];
    if !STATUSES.contains(&st.as_str()) {
        return Err(err(format!("status must be one of {:?}", STATUSES)));
    }
    let state = fresh_cache(root, false, false, true)?;
    let row = state
        .rows
        .iter()
        .find(|row| valstr(row.get("id")) == *cid)
        .ok_or_else(|| err(format!("conversation not found: {}", cid)))?;
    let (c, old_bytes) = read_row_conv(root, row)?;
    let mut m = c.meta.clone();
    m.insert("status".into(), json!(st));
    m.insert("updated".into(), json!(now_utc()));
    let next = Conv {
        path: c.path.clone(),
        meta: m,
        body: c.body.clone(),
    };
    let (writes, _) = reconcile_writes(root, &state, vec![(Some(c), next, old_bytes)])?;
    let ch = !writes.is_empty();
    let n = commit_writes(root, writes, &state, true)?.len();
    output(&json!({"id":cid,"status":st,"changed":ch,"index_records":n}));
    Ok(())
}
fn cmd_branch(root: &Path, args: &[String], side: bool) -> Result<(), ConvError> {
    if args.is_empty() {
        return Err(err("missing parent"));
    };
    let state = fresh_cache(root, false, false, true)?;
    let parent_row = resolve_row(&state, &args[0])?;
    let (parent, parent_bytes) = read_row_conv(root, parent_row)?;
    let mut topic = None;
    let mut nid = None;
    let mut keep = false;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--topic" => {
                i += 1;
                if i < args.len() {
                    topic = Some(args[i].clone())
                }
            }
            "--id" => {
                i += 1;
                if i < args.len() {
                    nid = Some(args[i].clone())
                }
            }
            "--keep-parent-active" => keep = true,
            _ => {}
        }
        i += 1
    }
    let topic = topic.unwrap_or_else(|| {
        if side {
            "sidekick".into()
        } else {
            format!("{} continued", valstr(parent.meta.get("topic")))
        }
    });
    let rel = if side {
        "spawned-from"
    } else {
        "continued-from"
    };
    let ps = sections_allow_dup(&parent.body)
        .map_err(|error| err(format!("{}: {}", id(&parent), error)))?;
    let qa = if side {
        "- **Q (open):** What should this sidekick resolve?\n  **A:** (none)".into()
    } else {
        ps.get("qa").cloned().unwrap_or_else(|| NONE.into())
    };
    let mut sec = BTreeMap::new();
    sec.insert(
        "summary".into(),
        format!(
            "{} of {}: {}",
            if side { "Sidekick" } else { "Continuation" },
            id(&parent),
            topic
        ),
    );
    sec.insert(
        "glossary".into(),
        ps.get("glossary").cloned().unwrap_or_else(|| NONE.into()),
    );
    sec.insert("qa".into(), qa);
    sec.insert("resume".into(),ps.get("resume").cloned().unwrap_or_else(||format!("- goal: {}\n- next-steps:\n  - Capture progress and save the record\n- open-questions:\n  - {}\n- suggested-skills:\n  - relay:save",if side{format!("Explore {}",topic)}else{format!("Continue {}",valstr(parent.meta.get("topic")))},topic)));
    let mut src = vec![
        format!(
            "- {}: {}",
            if side {
                "spawned-from"
            } else {
                "continued-from"
            },
            id(&parent)
        ),
        format!("- parent-topic: {}", valstr(parent.meta.get("topic"))),
    ];
    if !side {
        if let Some(s) = ps.get("sources") {
            src.extend(
                s.lines()
                    .filter(|l| !l.trim().is_empty())
                    .map(str::to_string),
            )
        }
    }
    sec.insert("sources".into(), src.join("\n"));
    for n in [
        "user-instructions",
        "insights",
        "decisions",
        "environment",
        "condensed-transcript",
    ] {
        if let Some(s) = ps.get(n) {
            if !s.is_empty() {
                sec.insert(n.into(), s.clone());
            }
        }
    }
    let mut raw = Map::new();
    raw.insert("topic".into(), json!(topic));
    raw.insert("status".into(), json!("active"));
    raw.insert(
        "tags".into(),
        parent.meta.get("tags").cloned().unwrap_or(json!([])),
    );
    raw.insert("refs".into(), json!([{"id":id(&parent),"rel":rel}]));
    raw.insert(
        "sections".into(),
        Value::Object(sec.into_iter().map(|(k, v)| (k, json!(v))).collect()),
    );
    if parent.meta.get("relay_schema").and_then(Value::as_u64) == Some(2) {
        raw.insert("relay_schema".into(), json!(2));
    }
    if let Some(n) = nid {
        raw.insert("id".into(), json!(n));
    }
    let known_ids = state
        .rows
        .iter()
        .map(|row| valstr(row.get("id")))
        .collect::<HashSet<_>>();
    let child_meta = normalize_meta(&raw, None, root, Some(&known_ids))?;
    let child_id = id_from(&child_meta);
    if known_ids.contains(&child_id) {
        return Err(err(format!("conversation already exists: {}", child_id)));
    }
    let child_body = build_body(&raw)?;
    let child_path = path_for(root, &child_id)?;
    let child = Conv {
        path: child_path.clone(),
        meta: child_meta,
        body: child_body,
    };
    let mut changes = vec![(None, child, Vec::new())];
    let pstat = if !side || !keep {
        let mut m = parent.meta.clone();
        m.insert("status".into(), json!("parked"));
        m.insert("updated".into(), json!(now_utc()));
        changes.push((
            Some(parent.clone()),
            Conv {
                path: parent.path.clone(),
                meta: m,
                body: parent.body.clone(),
            },
            parent_bytes,
        ));
        Some(json!({"id":id(&parent),"status":"parked","changed":true}))
    } else {
        None
    };
    let (writes, ref_changes) = reconcile_writes(root, &state, changes)?;
    let child_relative = relative_record_path(root, &child_path)?;
    let changed = writes.iter().any(|write| write.relative == child_relative);
    let records = commit_writes(root, writes, &state, true)?;
    output(
        &json!({"id":child_id,"file":child_relative,"parent":id(&parent),"status":"active","parent_status":pstat,"ref_changes":ref_changes,"index_records":records.len(),"changed":changed}),
    );
    Ok(())
}
fn cmd_return(root: &Path, args: &[String]) -> Result<(), ConvError> {
    if args.is_empty() {
        return Err(err("missing branch"));
    };
    let digest = args
        .windows(2)
        .find(|w| w[0] == "--digest")
        .map(|w| w[1].trim().to_string())
        .unwrap_or_default();
    if digest.is_empty() {
        return Err(err("--digest must not be empty"));
    };
    let state = fresh_cache(root, false, false, true)?;
    let branch_row = resolve_row(&state, &args[0])?;
    let (branch, branch_bytes) = read_row_conv(root, branch_row)?;
    let refs = normalize_refs(branch.meta.get("refs"))?;
    let mut parents: Vec<_> = refs
        .iter()
        .filter(|r| ["spawned-from", "continued-from"].contains(&valstr(r.get("rel")).as_str()))
        .map(|r| valstr(r.get("id")))
        .collect();
    parents.sort();
    parents.dedup();
    let explicit = args
        .windows(2)
        .find(|w| w[0] == "--parent")
        .map(|w| w[1].clone());
    let pid = if let Some(p) = explicit {
        if !parents.contains(&p) {
            return Err(err(format!(
                "{} is not a branch parent of {}: {:?}",
                p,
                id(&branch),
                parents
            )));
        }
        p
    } else if parents.len() == 1 {
        parents[0].clone()
    } else if parents.is_empty() {
        return Err(err(format!(
            "conversation has no branch parent ref: {}",
            id(&branch)
        )));
    } else {
        return Err(err(format!(
            "conversation has multiple branch parent refs: {:?}",
            parents
        )));
    };
    if !state.rows.iter().any(|row| valstr(row.get("id")) == pid) {
        return Err(err(format!("branch parent not found: {}", pid)));
    };
    let mut sec = sections_allow_dup(&branch.body)
        .map_err(|error| err(format!("{}: {}", id(&branch), error)))?;
    for n in MANDATORY {
        if !sec.contains_key(*n) {
            return Err(err(format!(
                "branch body missing mandatory sections: {}",
                n
            )));
        }
    }
    let changed_digest = sec.get("digest").map(|s| s.as_str()) != Some(&digest);
    sec.insert("digest".into(), digest);
    let body = canonical(&sec, None);
    let mut m = branch.meta.clone();
    let status_changed = valstr(m.get("status")) != "closed";
    let changed = changed_digest || status_changed || branch.body.trim_end() != body.trim_end();
    let changes = if changed {
        m.insert("status".into(), json!("closed"));
        m.insert("updated".into(), json!(now_utc()));
        vec![(
            Some(branch.clone()),
            Conv {
                path: branch.path.clone(),
                meta: m,
                body,
            },
            branch_bytes,
        )]
    } else {
        Vec::new()
    };
    let (writes, rc) = reconcile_writes(root, &state, changes)?;
    let n = commit_writes(root, writes, &state, true)?.len();
    output(
        &json!({"id":id(&branch),"parent":pid,"status":"closed","digest_changed":changed_digest,"changed":changed,"ref_changes":rc,"index_records":n}),
    );
    Ok(())
}
fn cmd_list(root: &Path, args: &[String]) -> Result<(), ConvError> {
    let lim = opt_limit(args)?;
    let mut st = None;
    let js = args.iter().any(|x| x == "--json");
    for i in 0..args.len() {
        if args[i] == "--status" && i + 1 < args.len() {
            st = Some(args[i + 1].clone())
        }
    }
    let mut r = read_index(root, true)?;
    if let Some(s) = st {
        r.retain(|v| valstr(v.get("status")) == s)
    }
    r.sort_by(|left, right| {
        let status_rank = |value: &Value| match valstr(value.get("status")).as_str() {
            "active" => 0,
            "parked" => 1,
            "closed" => 2,
            _ => 3,
        };
        status_rank(left)
            .cmp(&status_rank(right))
            .then_with(|| valstr(right.get("updated")).cmp(&valstr(left.get("updated"))))
            .then_with(|| valstr(left.get("id")).cmp(&valstr(right.get("id"))))
    });
    r.truncate(lim);
    if js {
        output(&Value::Array(r))
    } else {
        println!("id | topic | status | updated | open");
        println!("---|-------|--------|---------|----");
        for v in r {
            println!(
                "{} | {} | {} | {} | {}",
                valstr(v.get("id")),
                valstr(v.get("topic")),
                valstr(v.get("status")),
                valstr(v.get("updated")),
                valstr(v.get("open"))
            )
        }
    }
    Ok(())
}

fn stage_missing_legacy_records(
    source_dir: &Path,
    source_base: &Path,
    destination_dir: &Path,
    copied: &mut Vec<String>,
    unchanged: &mut Vec<String>,
    collisions: &mut Vec<String>,
    writes: &mut Vec<RecordWrite>,
) -> Result<(), ConvError> {
    let mut entries = fs::read_dir(source_dir)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            continue;
        }
        if kind.is_dir() {
            stage_missing_legacy_records(
                &path,
                source_base,
                destination_dir,
                copied,
                unchanged,
                collisions,
                writes,
            )?;
            continue;
        }
        if !kind.is_file() || path.extension().and_then(|value| value.to_str()) != Some("md") {
            continue;
        }
        let relative = path
            .strip_prefix(source_base)
            .map_err(|_| err("could not derive a legacy record path"))?;
        let relative_text = relative
            .to_str()
            .ok_or_else(|| err("legacy record path is not valid UTF-8"))?
            .replace('\\', "/");
        let destination = destination_dir.join(relative);
        let source_bytes = fs::read(&path)?;
        if destination.exists() {
            if fs::read(&destination)? == source_bytes {
                unchanged.push(relative_text);
            } else {
                collisions.push(relative_text);
            }
            continue;
        }
        copied.push(relative_text.clone());
        writes.push(RecordWrite {
            relative: format!("convs/{}", relative_text),
            bytes: source_bytes,
        });
    }
    Ok(())
}

fn cmd_import(root: &Path, args: &[String]) -> Result<(), ConvError> {
    let from = args
        .windows(2)
        .find(|window| window[0] == "--from")
        .map(|window| window[1].clone())
        .ok_or_else(|| err("import requires --from <legacy-root>"))?;
    let source_root = root_from(Some(&from))?;
    if source_root == root {
        return Err(err(
            "legacy import source must differ from the Relay installation root",
        ));
    }
    let source_records = convs(&source_root);
    if !source_records.is_dir() {
        return Err(err(format!(
            "legacy source has no convs/ directory: {}",
            source_root.display()
        )));
    }

    ensure(root)?;
    write_gitignore(root)?;
    let state = fresh_cache(root, true, false, true)?;
    let destination_records = convs(root);
    let mut copied = Vec::new();
    let mut unchanged = Vec::new();
    let mut collisions = Vec::new();
    let mut writes = Vec::new();
    stage_missing_legacy_records(
        &source_records,
        &source_records,
        &destination_records,
        &mut copied,
        &mut unchanged,
        &mut collisions,
        &mut writes,
    )?;
    copied.sort();
    unchanged.sort();
    collisions.sort();
    let records = commit_writes(root, writes, &state, true)?.len();
    output(&json!({
        "source_root": source_root,
        "copied": copied,
        "unchanged": unchanged,
        "collisions": collisions,
        "records": records,
    }));
    Ok(())
}

fn run_installer_repair(root: &Path) -> Value {
    let installed = root.join("scripts").join("install.py");
    let script = installed;
    let available = script.is_file();
    let mut o = json!({"available":available,"command":[],"returncode":Value::Null,"stdout":[],"stderr":[]});
    if !available {
        o["reason"]=json!("scripts/install.py not found under the Plugin installation root; reinstall Relay to restore installer repair");
        return o;
    }
    let source = script
        .parent()
        .and_then(|p| p.parent())
        .unwrap_or(Path::new("."));
    let tail = vec![
        script.to_string_lossy().to_string(),
        "--source".into(),
        source.to_string_lossy().to_string(),
        "--target".into(),
        root.to_string_lossy().to_string(),
        "--doctor-fix".into(),
    ];
    let mut candidates: Vec<Vec<String>> = Vec::new();
    if let Ok(x) = env::var("PYTHON") {
        candidates.push(vec![x]);
    }
    if let Ok(x) = env::var("PYTHON3") {
        candidates.push(vec![x]);
    }
    if cfg!(windows) {
        candidates.extend([
            vec!["python".into()],
            vec!["py".into(), "-3".into()],
            vec!["py".into()],
        ]);
    } else {
        candidates.extend([vec!["python3".into()], vec!["python".into()]]);
    }
    for mut c in candidates {
        let exe = c.remove(0);
        let mut full = c.clone();
        full.extend(tail.clone());
        match process::Command::new(&exe).args(&full).output() {
            Ok(x) => {
                let mut cmd = vec![exe];
                cmd.extend(full);
                o["command"] = json!(cmd);
                o["returncode"] = json!(x.status.code());
                o["stdout"] = json!(String::from_utf8_lossy(&x.stdout)
                    .lines()
                    .map(String::from)
                    .collect::<Vec<_>>());
                o["stderr"] = json!(String::from_utf8_lossy(&x.stderr)
                    .lines()
                    .map(String::from)
                    .collect::<Vec<_>>());
                return o;
            }
            Err(_) => continue,
        }
    }
    o["reason"] = json!("no available Python executable found");
    o
}

fn resume_group_has_item(markdown: &str, group: &str) -> bool {
    let marker = format!("- {}:", group);
    let mut inside = false;
    for line in markdown.lines() {
        if line.trim() == marker {
            inside = true;
            continue;
        }
        if inside && line.starts_with("- ") {
            break;
        }
        if inside && line.trim_start().starts_with("- ") {
            let value = line.trim_start().trim_start_matches("- ").trim();
            if !value.is_empty() && value != NONE {
                return true;
            }
        }
    }
    false
}

fn fidelity_lint(conv: &Conv) -> Option<Value> {
    let values = sections_allow_dup(&conv.body).unwrap_or_default();
    let resume = values.get("resume").map(String::as_str).unwrap_or("");
    let has_goal = resume
        .lines()
        .find_map(|line| line.trim().strip_prefix("- goal:"))
        .is_some_and(|goal| !goal.trim().is_empty() && goal.trim() != NONE);
    let has_next = resume_group_has_item(resume, "next-steps");
    let has_glossary = values.get("glossary").is_some_and(|markdown| {
        markdown.lines().any(|line| {
            let line = line.trim();
            line.starts_with("- ") && line.trim_start_matches("- ").trim() != NONE
        })
    });
    let has_instructions = values.get("user-instructions").is_some_and(|markdown| {
        markdown.lines().any(|line| {
            let value = line.trim().trim_start_matches("- ").trim();
            !value.is_empty() && value != NONE
        })
    });
    let transcript_count = transcript_exchanges(
        values
            .get("condensed-transcript")
            .map(String::as_str)
            .unwrap_or(""),
        conv.meta.get("relay_schema").and_then(Value::as_u64) == Some(2),
    )
    .len();
    let checks = [
        ("resume-goal", has_goal),
        ("next-step", has_next),
        ("glossary-entry", has_glossary),
        ("user-instructions", has_instructions),
        ("transcript-entries", transcript_count >= 3),
    ];
    let score = checks.iter().filter(|(_, present)| *present).count();
    (score <= 2).then(|| json!({
        "file":conv.path,
        "fidelity":score,
        "missing":checks.iter().filter(|(_,present)| !*present).map(|(name,_)| *name).collect::<Vec<_>>(),
    }))
}

fn raw_index_health(root: &Path) -> Value {
    let text = match fs::read_to_string(index_path(root)) {
        Ok(text) => text,
        Err(error) => return json!({"valid":false,"records":0,"error":error.to_string()}),
    };
    let mut records = 0usize;
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: Value = match serde_json::from_str(line) {
            Ok(row) => row,
            Err(error) => {
                return json!({"valid":false,"records":0,"error":format!("{} has malformed JSON on line {}: {}",index_path(root).display(),index + 1,error)})
            }
        };
        if !valid_index(root, &row) {
            return json!({"valid":false,"records":0,"error":format!("{} has invalid index record on line {}: missing relay record",index_path(root).display(),index + 1)});
        }
        records += 1;
    }
    json!({"valid":true,"records":records,"error":Value::Null})
}
fn cmd_doctor(root: &Path, args: &[String], compat: bool) -> Result<(), ConvError> {
    let ignored = vec!["RELAY_ROOT", "CONVERSATE_ROOT", "BRAIN_CONV"]
        .into_iter()
        .filter(|k| env::var(k).is_ok())
        .map(String::from)
        .collect::<Vec<_>>();
    let fix = args.iter().any(|x| x == "--fix");
    let semble_available = tool_available("semble");
    let uvx_available = tool_available("uvx");
    let tools = json!({
        "rg": tool_available("rg"),
        "fff": tool_available("fff"),
        "semble": semble_available,
        "uvx": uvx_available,
        "python": tool_available("python"),
    });
    let semantic_search = if semble_available {
        "semble"
    } else if uvx_available && use_uvx_semble() {
        "uvx semble (set RELAY_USE_UVX_SEMBLE=1)"
    } else {
        "body fallback"
    };
    ensure(root)?;
    let mut parse = Vec::new();
    let mut warn = Vec::new();
    let mut canonical_records = Vec::new();
    let mut records = 0;
    let engine = ScanEngine::configured()?;
    let doctor_snapshot = engine.snapshot(root)?;
    let mut doctor_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for stat in doctor_snapshot {
        let p = stat.path.clone();
        let bytes = match fs::read(&p) {
            Ok(bytes) => bytes,
            Err(error) => {
                parse.push(json!({"file":p,"error":error.to_string()}));
                continue;
            }
        };
        trace_record("record_open", root, &p, 0, bytes.len());
        match read_conv_bytes(&p, &bytes) {
            Ok(mut c) => {
                doctor_ids
                    .entry(id(&c))
                    .or_default()
                    .push(stat.relative.clone());
                let ss = match sections_allow_dup(&c.body) {
                    Ok(ss) => ss,
                    Err(_) => {
                        warn.push(json!({"file":p,"conflicting_sections":["dict","glossary"]}));
                        records += 1;
                        continue;
                    }
                };
                if fix && duplicates(&c.body).is_empty()
                    && MANDATORY.iter().all(|n| ss.contains_key(*n))
                {
                    let b = canonical(&ss, None);
                    if c.body.trim_end() != b.trim_end() {
                        let mut m = c.meta.clone();
                        m.insert("updated".into(), json!(now_utc()));
                        write_conv(&c.path, &m, &b)?;
                        canonical_records.push(
                            c.path
                                .strip_prefix(root)
                                .unwrap_or(&c.path)
                                .to_string_lossy()
                                .replace('\\', "/"),
                        );
                        c.meta = m;
                        c.body = b;
                    }
                }
                records += 1;
                let d = duplicates(&c.body);
                if !d.is_empty() {
                    warn.push(json!({"file":p,"duplicate_sections":d}))
                }
                let ss = sections_allow_dup(&c.body).unwrap_or(ss);
                let missing: Vec<_> = ALWAYS
                    .iter()
                    .filter(|n| !ss.contains_key(**n))
                    .map(|n| json!(n))
                    .collect();
                if !missing.is_empty() {
                    warn.push(json!({"file":p,"missing_sections":missing}))
                }
                if !fix && missing.is_empty() {
                    if let Some(fidelity) = fidelity_lint(&c) {
                        warn.push(fidelity);
                    }
                }
            }
            Err(e) => parse.push(json!({"file":p,"error":e.to_string()})),
        }
    }
    for (record_id, paths) in doctor_ids.into_iter().filter(|(_, paths)| paths.len() > 1) {
        warn.push(json!({"id":record_id,"duplicate_paths":paths}));
    }
    let mut f = json!({"enabled":fix,"layout":false,"gitignore":false,"canonical_records":[],"ref_changes":0,"index_records":Value::Null});
    if fix {
        let gitignore_changed = repair_gitignore(root)?;
        let (rc, n) = regen_mode(root, true)?;
        f["gitignore"] = json!(gitignore_changed);
        f["canonical_records"] = json!(canonical_records);
        f["ref_changes"] = json!(rc);
        f["index_records"] = json!(n);
        let installer = run_installer_repair(root);
        f["installer_repair"] = installer.clone();
        if installer.get("available") == Some(&Value::Bool(false)) {
            warn.push(json!({"installer_repair":"unavailable","reason":installer.get("reason")}))
        } else if installer.get("returncode").and_then(|x| x.as_i64()) != Some(0) {
            warn.push(json!({"installer_repair":"failed","returncode":installer.get("returncode"),"stderr":installer.get("stderr")}));
        }
    }
    let health = raw_index_health(root);
    let relay_archive = convs(root);
    output(&json!({
        "plugin_installation_root": root,
        "relay_archive": relay_archive,
        "conversation_database": relay_archive,
        "deprecated": {"aliases": {"conv_root": root, "convs": relay_archive, "conversation_database": relay_archive}},
        "resolution": {
            "layer": if compat {"compat-flag"} else {"default-global"},
            "compatibility": compat,
            "ignored_legacy_env": ignored,
        },
        "layout": {
            "convs": true,
            "index": index_path(root).exists(),
            "semble_cache": root.join(".semble").exists(),
            "gitignore": root.join(".gitignore").exists(),
        },
        "tools": tools,
        "semantic_search": semantic_search,
        "parse_errors": parse,
        "warnings": warn,
        "records": records,
        "index_health": health,
        "fix": f,
    }));
    Ok(())
}

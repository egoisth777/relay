mod atomic_io;
mod hook_runtime;
mod search_backend;

use atomic_io::{lock_exclusive, write_atomic, ExclusiveLock};
use search_backend::{search_semble, tool_available};

use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process;
use std::time::{SystemTime, UNIX_EPOCH};

const STATUSES: &[&str] = &["active", "parked", "closed"];
const MANDATORY: &[&str] = &["summary", "dict", "qa"];
const ALWAYS: &[&str] = &["resume", "user-instructions", "condensed-transcript"];
const ORDER: &[&str] = &[
    "summary",
    "dict",
    "qa",
    "sources",
    "insights",
    "decisions",
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
fn root_from(arg: Option<&String>) -> PathBuf {
    let p = arg
        .map(|s| {
            let q = PathBuf::from(s);
            if q.is_absolute() {
                q
            } else {
                env::current_dir().unwrap_or_default().join(q)
            }
        })
        .unwrap_or_else(|| {
            env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".relay")
        });
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
    out
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
fn mutation_lock(root: &Path) -> Result<ExclusiveLock, ConvError> {
    if root.exists() && !root.is_dir() {
        return Err(err(format!(
            "Plugin installation root must be a directory, not a file: {}",
            root.display()
        )));
    }
    let cache = root.join(".semble");
    fs::create_dir_all(&cache)?;
    Ok(lock_exclusive(&cache.join("write.lock"))?)
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
    let re = Regex::new(r"^conv_(\d{2})(\d{2})(\d{2})_(.+)$").unwrap();
    if let Some(c) = re.captures(id) {
        format!("20{}-{}-{}_{}.md", &c[1], &c[2], &c[3], &c[4])
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
    let text = fs::read_to_string(path)?;
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
        body,
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
    let mut out = Vec::new();
    for e in fs::read_dir(convs(root))? {
        let p = e?.path();
        if p.extension().and_then(|x| x.to_str()) == Some("md") {
            match read_conv(&p) {
                Ok(c) => out.push(c),
                Err(e) => {
                    if !tolerate {
                        return Err(e);
                    }
                }
            }
        }
    }
    out.sort_by_key(|c| c.path.clone());
    Ok(out)
}
fn id(c: &Conv) -> String {
    valstr(c.meta.get("id"))
}
fn find(root: &Path, target: &str) -> Result<Option<Conv>, ConvError> {
    if valid_id(target).is_ok() {
        let direct = path_for(root, target)?;
        if direct.is_file() {
            let conv = read_conv(&direct)?;
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
fn sections(body: &str) -> Result<BTreeMap<String, String>, ConvError> {
    let re = Regex::new(r"(?m)^##\s+(.+?)\s*$").unwrap();
    let ms: Vec<_> = re.captures_iter(body).collect();
    let mut out = BTreeMap::new();
    for i in 0..ms.len() {
        let name = ms[i][1].trim().to_lowercase();
        if out.contains_key(&name) {
            return Err(err(format!("duplicate section(s): {}", name)));
        }
        let start = ms[i].get(0).unwrap().end();
        let end = if i + 1 < ms.len() {
            ms[i + 1].get(0).unwrap().start()
        } else {
            body.len()
        };
        out.insert(name, body[start..end].trim().to_string());
    }
    Ok(out)
}
fn sections_allow_dup(body: &str) -> BTreeMap<String, String> {
    let re = Regex::new(r"(?m)^##\s+(.+?)\s*$").unwrap();
    let ms: Vec<_> = re.captures_iter(body).collect();
    let mut out = BTreeMap::new();
    for i in 0..ms.len() {
        let name = ms[i][1].trim().to_lowercase();
        let start = ms[i].get(0).unwrap().end();
        let end = if i + 1 < ms.len() {
            ms[i + 1].get(0).unwrap().start()
        } else {
            body.len()
        };
        out.insert(name, body[start..end].trim().to_string());
    }
    out
}
fn duplicates(body: &str) -> Vec<String> {
    let re = Regex::new(r"(?m)^##\s+(.+?)\s*$").unwrap();
    let mut seen = HashSet::new();
    let mut d = HashSet::new();
    for c in re.captures_iter(body) {
        let n = c[1].trim().to_lowercase();
        if !seen.insert(n.clone()) {
            d.insert(n);
        }
    }
    let mut v: Vec<_> = d.into_iter().collect();
    v.sort();
    v
}
fn count_open(body: &str) -> i64 {
    let s = sections_allow_dup(body).remove("qa").unwrap_or_default();
    let re = Regex::new(r"(?i)\bq\s*\(open\)|\bopen\s*:").unwrap();
    s.lines().filter(|l| re.is_match(l)).count() as i64
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
            let u = valstr(o.get("u")).trim().to_string();
            let aa = valstr(o.get("a")).trim().to_string();
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
        return Ok(canonical(&s, Some(&always)));
    }
    let Some(Value::Object(o)) = raw.get("sections") else {
        return Err(err("sections object is required when body is not provided"));
    };
    for (k, v) in o {
        let s = norm_section(Some(v));
        if !s.is_empty() {
            sec.insert(k.trim().to_lowercase(), s);
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
        while find(root, &x)?.is_some() {
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
    Ok(m)
}
fn index_record(root: &Path, c: &Conv) -> Result<Value, ConvError> {
    Ok(
        json!({"id":id(c),"topic":valstr(c.meta.get("topic")),"status":valstr(c.meta.get("status")),"tags":c.meta.get("tags").cloned().unwrap_or(json!([])),"refs":normalize_refs(c.meta.get("refs"))?,"created":iso(c.meta.get("created")),"updated":iso(c.meta.get("updated")),"file":c.path.strip_prefix(root).unwrap_or(&c.path).to_string_lossy().replace('\\',"/"),"open":count_open(&c.body)}),
    )
}
fn rebuild(root: &Path, tolerate: bool) -> Result<Vec<Value>, ConvError> {
    ensure(root)?;
    let mut r = Vec::new();
    for c in all_convs(root, tolerate)? {
        r.push(index_record(root, &c)?)
    }
    r.sort_by_key(|v| valstr(v.get("id")));
    write_index(root, &r)?;
    Ok(r)
}
fn write_index(root: &Path, records: &[Value]) -> Result<(), ConvError> {
    let text = records
        .iter()
        .map(|record| serde_json::to_string(record).map(|line| format!("{line}\n")))
        .collect::<Result<String, _>>()
        .map_err(|error| err(error.to_string()))?;
    write_atomic(&index_path(root), text.as_bytes())?;
    Ok(())
}
fn read_index(root: &Path, tolerate: bool) -> Result<Vec<Value>, ConvError> {
    ensure(root)?;
    let text = fs::read_to_string(index_path(root))?;
    let mut r = Vec::new();
    for (i, l) in text.lines().enumerate() {
        if l.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(l) {
            Ok(v) => {
                if !valid_index(root, &v) {
                    if tolerate {
                        return Ok(vec![]);
                    } else {
                        return Err(err(format!(
                            "{} has invalid index record on line {}: missing relay record",
                            index_path(root).display(),
                            i + 1
                        )));
                    }
                }
                r.push(v)
            }
            Err(e) => {
                if !tolerate {
                    return Err(err(format!(
                        "{} has malformed JSON on line {}: {}",
                        index_path(root).display(),
                        i + 1,
                        e
                    )));
                }
            }
        }
    }
    Ok(r)
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
    let rid = raw.get("id").map(|v| valstr(Some(v))).unwrap_or_default();
    let existing = if rid.is_empty() {
        None
    } else {
        find(root, &rid)?
    };
    if create_only && existing.is_some() {
        return Err(err(format!("conversation already exists: {}", rid)));
    }
    let mut raw2 = raw.clone();
    if let Some(s) = override_status {
        raw2.insert("status".into(), json!(s));
    }
    let meta = normalize_meta(&raw2, existing.as_ref().map(|c| &c.meta), root)?;
    let body = build_body(&raw2)?;
    let path = existing
        .as_ref()
        .map(|c| c.path.clone())
        .unwrap_or(path_for(root, &id_from(&meta))?);
    let changed = write_conv(&path, &meta, &body)?;
    let conv = Conv {
        path: path.clone(),
        meta: meta.clone(),
        body: body.clone(),
    };
    let refs = normalize_refs(meta.get("refs"))?;
    let old_refs = existing
        .as_ref()
        .map(|c| normalize_refs(c.meta.get("refs")).unwrap_or_default())
        .unwrap_or_default();
    let records = if !refs.is_empty() || !old_refs.is_empty() {
        regen(root)?;
        rebuild(root, false)?
    } else {
        let mut x = read_index(root, true)?;
        x.retain(|v| valstr(v.get("id")) != id_from(&meta));
        x.push(index_record(root, &conv)?);
        x.sort_by_key(|v| valstr(v.get("id")));
        write_index(root, &x)?;
        x
    };
    Ok(
        json!({"id":id_from(&meta),"file":path.strip_prefix(root).unwrap_or(&path).to_string_lossy().replace('\\',"/"),"changed":changed,"ref_changes":0,"index_records":records.len()}),
    )
}
fn id_from(m: &BTreeMap<String, Value>) -> String {
    valstr(m.get("id"))
}
fn regen(root: &Path) -> Result<usize, ConvError> {
    let cs = all_convs(root, true)?;
    let ids: HashSet<_> = cs.iter().map(id).collect();
    let mut desired: HashMap<String, HashSet<(String, String)>> = HashMap::new();
    for c in &cs {
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
    for c in &cs {
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
    let mut changed = 0;
    for c in cs {
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
            write_conv(&c.path, &m, &c.body)?;
            changed += 1;
        }
    }
    Ok(changed)
}
fn resolve(root: &Path, target: &str) -> Result<Conv, ConvError> {
    if let Some(c) = find(root, target)? {
        return Ok(c);
    };
    let hits = search(root, target, 5)?;
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
    let re = Regex::new(r"[a-z0-9]+").unwrap();
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
    re.find_iter(&q.to_lowercase())
        .map(|m| m.as_str().to_string())
        .filter(|s| !stop.contains(&s.as_str()))
        .collect()
}
fn search(root: &Path, q: &str, limit: usize) -> Result<Vec<Value>, ConvError> {
    if limit == usize::MAX {
        return Err(err("--limit must be >= 0"));
    };
    let mut rec = read_index(root, true)?;
    if rec.is_empty() {
        rec = rebuild(root, false)?;
    }
    let mut ts = terms(q);
    if ts.is_empty() && !q.trim().is_empty() {
        ts.push(q.to_lowercase().trim().into())
    };
    let score = |text: &str| {
        let lower = text.to_lowercase();
        ts.iter().filter(|term| lower.contains(*term)).count() as i64
    };
    let mut hits: Vec<Value> = rec
        .iter()
        .filter_map(|r| {
            let s = score(&format!(
                "{} {}",
                valstr(r.get("id")),
                valstr(r.get("file"))
            ));
            if s > 0 {
                let mut o = r.as_object().unwrap().clone();
                o.insert("layer".into(), json!("fff"));
                o.insert("score".into(), json!(s));
                Some(Value::Object(o))
            } else {
                None
            }
        })
        .collect();
    if hits.len() == 1 {
        return Ok(hits);
    };
    if !hits.is_empty() {
        hits.sort_by(|a, b| {
            b["score"]
                .as_i64()
                .cmp(&a["score"].as_i64())
                .then_with(|| valstr(b.get("updated")).cmp(&valstr(a.get("updated"))))
        });
        hits.truncate(limit);
        return Ok(hits);
    };
    let mut ih = Vec::new();
    for r in &rec {
        let s = score(&serde_json::to_string(r).unwrap());
        if s > 0 {
            let mut o = r.as_object().unwrap().clone();
            o.insert("layer".into(), json!("rg-index-fallback"));
            o.insert("score".into(), json!(s));
            ih.push(Value::Object(o))
        }
    }
    if ih.len() == 1 {
        return Ok(ih);
    };
    if !ih.is_empty() {
        ih.sort_by(|a, b| {
            b["score"]
                .as_i64()
                .cmp(&a["score"].as_i64())
                .then_with(|| valstr(b.get("updated")).cmp(&valstr(a.get("updated"))))
        });
        ih.truncate(limit);
        return Ok(ih);
    };
    let semble_hits = search_semble(root, &rec, q, limit);
    if !semble_hits.is_empty() {
        return Ok(semble_hits);
    }

    let mut bh = Vec::new();
    for c in all_convs(root, true)? {
        let s = score(&c.body);
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
    let root = root_from(rootarg.as_ref());
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
    let _lock = mutates_store.then(|| mutation_lock(root)).transpose()?;

    match cmd {
        "init" => {
            ensure(root)?;
            write_gitignore(root)?;
            let r = rebuild(root, false)?;
            output(
                &json!({"plugin_installation_root":root,"conversation_database":convs(root),"deprecated":{"aliases":{"conv_root":root,"convs":convs(root)}},"index":index_path(root),"records":r.len()}),
            );
        }
        "rebuild-index" => {
            output(&json!({"records":rebuild(root,false)?.len()}));
        }
        "regen-refs" => {
            let c = regen(root)?;
            let n = rebuild(root, false)?.len();
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
            output(&Value::Array(search(root, &args[0], lim)?));
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
        "doctor" => cmd_doctor(root, args, compat)?,
        _ => return Err(err(format!("invalid choice: {}", cmd))),
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
    let c = find(root, cid)?.ok_or_else(|| err(format!("conversation not found: {}", cid)))?;
    let mut m = c.meta.clone();
    m.insert("status".into(), json!(st));
    m.insert("updated".into(), json!(now_utc()));
    let ch = write_conv(&c.path, &m, &c.body)?;
    let n = rebuild(root, false)?.len();
    output(&json!({"id":cid,"status":st,"changed":ch,"index_records":n}));
    Ok(())
}
fn cmd_branch(root: &Path, args: &[String], side: bool) -> Result<(), ConvError> {
    if args.is_empty() {
        return Err(err("missing parent"));
    };
    let parent = resolve(root, &args[0])?;
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
    let ps = sections_allow_dup(&parent.body);
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
        "dict".into(),
        ps.get("dict").cloned().unwrap_or_else(|| NONE.into()),
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
    for n in ["user-instructions", "insights", "decisions"] {
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
    if let Some(n) = nid {
        raw.insert("id".into(), json!(n));
    }
    let result = upsert(root, raw, None, true)?;
    let pstat = if !side || !keep {
        let mut m = parent.meta.clone();
        m.insert("status".into(), json!("parked"));
        m.insert("updated".into(), json!(now_utc()));
        let ch = write_conv(&parent.path, &m, &parent.body)?;
        regen(root)?;
        Some(
            json!({"id":id(&parent),"status":"parked","changed":ch,"index_records":rebuild(root,false)?.len()}),
        )
    } else {
        None
    };
    output(
        &json!({"id":result["id"],"file":result["file"],"parent":id(&parent),"status":"active","parent_status":pstat,"ref_changes":result["ref_changes"],"index_records":pstat.as_ref().and_then(|x|x.get("index_records")).cloned().unwrap_or(result["index_records"].clone()),"changed":result["changed"]}),
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
    let branch = resolve(root, &args[0])?;
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
    if find(root, &pid)?.is_none() {
        return Err(err(format!("branch parent not found: {}", pid)));
    };
    let mut sec = sections_allow_dup(&branch.body);
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
    let changed = if changed_digest || status_changed || branch.body.trim_end() != body.trim_end() {
        m.insert("status".into(), json!("closed"));
        m.insert("updated".into(), json!(now_utc()));
        write_conv(&branch.path, &m, &body)?
    } else {
        false
    };
    let rc = regen(root)?;
    let n = rebuild(root, false)?.len();
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
    if r.is_empty() {
        r = rebuild(root, false)?;
    }
    if let Some(s) = st {
        r.retain(|v| valstr(v.get("status")) == s)
    }
    r.sort_by_key(|v| valstr(v.get("updated")));
    r.reverse();
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

fn copy_missing_legacy_records(
    source_dir: &Path,
    source_base: &Path,
    destination_dir: &Path,
    copied: &mut Vec<String>,
    unchanged: &mut Vec<String>,
    collisions: &mut Vec<String>,
) -> Result<(), ConvError> {
    for entry in fs::read_dir(source_dir)? {
        let entry = entry?;
        let path = entry.path();
        let kind = entry.file_type()?;
        if kind.is_dir() {
            copy_missing_legacy_records(
                &path,
                source_base,
                destination_dir,
                copied,
                unchanged,
                collisions,
            )?;
            continue;
        }
        if !kind.is_file() || path.extension().and_then(|value| value.to_str()) != Some("md") {
            continue;
        }
        let relative = path
            .strip_prefix(source_base)
            .map_err(|_| err("could not derive a legacy record path"))?;
        let relative_text = relative.to_string_lossy().replace('\\', "/");
        let destination = destination_dir.join(relative);
        if destination.exists() {
            if fs::read(&path)? == fs::read(&destination)? {
                unchanged.push(relative_text);
            } else {
                collisions.push(relative_text);
            }
            continue;
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(&path, &destination)?;
        copied.push(relative_text);
    }
    Ok(())
}

fn cmd_import(root: &Path, args: &[String]) -> Result<(), ConvError> {
    let from = args
        .windows(2)
        .find(|window| window[0] == "--from")
        .map(|window| window[1].clone())
        .ok_or_else(|| err("import requires --from <legacy-root>"))?;
    let source_root = root_from(Some(&from));
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
    let destination_records = convs(root);
    let mut copied = Vec::new();
    let mut unchanged = Vec::new();
    let mut collisions = Vec::new();
    copy_missing_legacy_records(
        &source_records,
        &source_records,
        &destination_records,
        &mut copied,
        &mut unchanged,
        &mut collisions,
    )?;
    let records = rebuild(root, true)?.len();
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
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join("install.py");
    let script = if installed.is_file() { installed } else { dev };
    let available = script.is_file();
    let mut o = json!({"available":available,"command":[],"returncode":Value::Null,"stdout":[],"stderr":[]});
    if !available {
        o["reason"]=json!("scripts/install.py not found; reinstall Relay from a complete checkout to restore installer repair");
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
    } else if uvx_available && env::var("RELAY_USE_UVX_SEMBLE").as_deref() == Ok("1") {
        "uvx semble (set RELAY_USE_UVX_SEMBLE=1)"
    } else {
        "body fallback"
    };
    ensure(root)?;
    let mut parse = Vec::new();
    let mut warn = Vec::new();
    let mut canonical_records = Vec::new();
    let mut records = 0;
    for e in fs::read_dir(convs(root))? {
        let p = e?.path();
        if p.extension().and_then(|x| x.to_str()) != Some("md") {
            continue;
        }
        match read_conv(&p) {
            Ok(mut c) => {
                if fix && duplicates(&c.body).is_empty() {
                    let ss = sections_allow_dup(&c.body);
                    if MANDATORY.iter().all(|n| ss.contains_key(*n)) {
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
                            c = read_conv(&c.path)?;
                        }
                    }
                }
                records += 1;
                let d = duplicates(&c.body);
                if !d.is_empty() {
                    warn.push(json!({"file":p,"duplicate_sections":d}))
                }
                let ss = sections_allow_dup(&c.body);
                let missing: Vec<_> = ALWAYS
                    .iter()
                    .filter(|n| !ss.contains_key(**n))
                    .map(|n| json!(n))
                    .collect();
                if !missing.is_empty() {
                    warn.push(json!({"file":p,"missing_sections":missing}))
                }
            }
            Err(e) => parse.push(json!({"file":p,"error":e.to_string()})),
        }
    }
    let mut f = json!({"enabled":fix,"layout":false,"gitignore":false,"canonical_records":[],"ref_changes":0,"index_records":Value::Null});
    if fix {
        let gitignore_changed = repair_gitignore(root)?;
        let rc = regen(root)?;
        let n = rebuild(root, true)?.len();
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
    let health = read_index(root, false)
        .map(|r| json!({"valid":true,"records":r.len(),"error":Value::Null}))
        .unwrap_or_else(|e| json!({"valid":false,"records":0,"error":e.to_string()}));
    output(&json!({
        "plugin_installation_root": root,
        "conversation_database": convs(root),
        "deprecated": {"aliases": {"conv_root": root, "convs": convs(root)}},
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

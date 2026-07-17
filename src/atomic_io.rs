use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(windows)]
use std::thread;
#[cfg(windows)]
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// A process-wide exclusive lock held for the lifetime of this value.
pub struct ExclusiveLock {
    file: File,
}

/// A process-wide shared lock held for the lifetime of this value.
pub struct SharedLock {
    file: File,
}

/// Open and acquire an exclusive lock on `path`.
pub fn lock_exclusive(path: &Path) -> io::Result<ExclusiveLock> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    file.lock()?;
    Ok(ExclusiveLock { file })
}

/// Open an existing lock file and acquire a shared reader lock without creating it.
pub fn lock_shared(path: &Path) -> io::Result<SharedLock> {
    let file = OpenOptions::new().read(true).write(true).open(path)?;
    File::lock_shared(&file)?;
    Ok(SharedLock { file })
}

impl Drop for ExclusiveLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

impl Drop for SharedLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

/// Replace `path` with `bytes` without truncating the existing target on failure.
///
/// The temporary file is created in the target's directory, so rename stays on one
/// filesystem and atomically replaces the previous target on supported filesystems.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let file_name = path.file_name().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "atomic write path must name a file",
        )
    })?;
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let process_id = std::process::id();

    loop {
        let sequence = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let temp_path = parent.join(format!(
            ".{}.{}.{}.tmp",
            file_name.to_string_lossy(),
            process_id,
            sequence
        ));
        let mut temp = match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp_path)
        {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        };

        let result = temp
            .write_all(bytes)
            .and_then(|_| temp.flush())
            .and_then(|_| temp.sync_all());
        drop(temp);
        let result = result
            .and_then(|_| replace_file(&temp_path, path))
            .and_then(|_| sync_parent(parent));
        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        return result;
    }
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

#[cfg(windows)]
const REPLACE_MAX_ATTEMPTS: usize = 5;

#[cfg(windows)]
const REPLACE_RETRY_BASE_DELAY: Duration = Duration::from_millis(5);

#[cfg(windows)]
// MoveFileExW can report ERROR_ACCESS_DENIED when a destination is transiently held
// without delete sharing; ERROR_SHARING_VIOLATION is the other sharing-specific code.
fn is_retryable_replace_error(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(5 | 32))
}

#[cfg(windows)]
fn replace_retry_delay(attempt: usize) -> Duration {
    let shift = attempt.min(3);
    REPLACE_RETRY_BASE_DELAY.saturating_mul(1 << shift)
}

#[cfg(windows)]
fn replace_file_with<F, S>(
    source: &Path,
    destination: &Path,
    mut move_file: F,
    mut sleep: S,
) -> io::Result<()>
where
    F: FnMut(&[u16], &[u16]) -> io::Result<()>,
    S: FnMut(Duration),
{
    let source = source
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();

    for attempt in 0..REPLACE_MAX_ATTEMPTS {
        match move_file(&source, &destination) {
            Ok(()) => return Ok(()),
            Err(error) if is_retryable_replace_error(&error) && attempt + 1 < REPLACE_MAX_ATTEMPTS => {
                sleep(replace_retry_delay(attempt));
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!("replacement loop always returns")
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    replace_file_with(
        source,
        destination,
        |source, destination| {
            let result = unsafe {
                MoveFileExW(
                    source.as_ptr(),
                    destination.as_ptr(),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
                )
            };
            if result == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(())
            }
        },
        thread::sleep,
    )
}

/// Sync the containing directory where the platform exposes a usable directory handle.
pub fn sync_parent(parent: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        File::open(parent)?.sync_all()
    }
    #[cfg(windows)]
    {
        // MoveFileExW with WRITE_THROUGH is the available barrier for the rename itself.
        // Opening directories for FlushFileBuffers requires backup-semantics handles, so
        // unsupported parent-directory sync is explicitly classified as a no-op here.
        let _ = parent;
        Ok(())
    }
}

/// Remove a durable artifact and apply the same parent-directory barrier.
pub fn remove_durable(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => sync_parent(path.parent().unwrap_or_else(|| Path::new("."))),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::write_atomic;
    #[cfg(windows)]
    use super::{replace_file_with, REPLACE_MAX_ATTEMPTS};
    use std::fs;
    #[cfg(windows)]
    use std::io;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::Ordering;
    #[cfg(windows)]
    use std::time::Duration;
    fn test_directory(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "relay-atomic-{name}-{}-{}",
            std::process::id(),
            super::TEMP_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).expect("create test directory");
        path
    }

    fn remove_test_directory(path: &Path) {
        fs::remove_dir_all(path).expect("remove test directory");
    }

    #[test]
    fn replaces_existing_file() {
        let directory = test_directory("replace");
        let target = directory.join("record");
        fs::write(&target, b"old contents").expect("write initial target");

        write_atomic(&target, b"new contents").expect("replace target");

        assert_eq!(fs::read(&target).expect("read target"), b"new contents");
        remove_test_directory(&directory);
    }

    #[test]
    fn failed_replacement_preserves_existing_target() {
        let directory = test_directory("failure");
        let target = directory.join("record");
        fs::create_dir(&target).expect("create target directory");
        let sentinel = target.join("sentinel");
        fs::write(&sentinel, b"untouched").expect("write sentinel");

        assert!(write_atomic(&target, b"replacement").is_err());

        assert_eq!(fs::read(&sentinel).expect("read sentinel"), b"untouched");
        assert_eq!(
            fs::read_dir(&directory)
                .expect("read test directory")
                .count(),
            1
        );
        remove_test_directory(&directory);
    }
    #[cfg(windows)]
    #[test]
    fn windows_replace_retries_only_sharing_errors_and_is_bounded() {
        for code in [5, 32] {
            let mut calls = 0;
            let mut sleeps = Vec::new();
            let result = replace_file_with(
                Path::new("source"),
                Path::new("destination"),
                |_source, _destination| {
                    calls += 1;
                    Err(io::Error::from_raw_os_error(code))
                },
                |delay| sleeps.push(delay),
            );

            assert_eq!(result.unwrap_err().raw_os_error(), Some(code));
            assert_eq!(calls, REPLACE_MAX_ATTEMPTS);
            assert_eq!(sleeps.len(), REPLACE_MAX_ATTEMPTS - 1);
            assert_eq!(
                sleeps,
                vec![
                    Duration::from_millis(5),
                    Duration::from_millis(10),
                    Duration::from_millis(20),
                    Duration::from_millis(40),
                ]
            );
        }

        let mut calls = 0;
        let mut sleeps = 0;
        let result = replace_file_with(
            Path::new("source"),
            Path::new("destination"),
            |_source, _destination| {
                calls += 1;
                Err(io::Error::from_raw_os_error(2))
            },
            |_delay| sleeps += 1,
        );

        assert_eq!(result.unwrap_err().raw_os_error(), Some(2));
        assert_eq!(calls, 1);
        assert_eq!(sleeps, 0);
    }
}

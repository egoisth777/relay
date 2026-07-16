use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// A process-wide exclusive lock held for the lifetime of this value.
pub struct ExclusiveLock {
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

impl Drop for ExclusiveLock {
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
            .and_then(|_| temp.sync_all())
            .and_then(|_| {
                drop(temp);
                fs::rename(&temp_path, path)
            });
        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        return result;
    }
}

#[cfg(test)]
mod tests {
    use super::write_atomic;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::Ordering;

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
}

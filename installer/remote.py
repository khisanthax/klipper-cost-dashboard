"""
SSH and remote operations for Print Cost Dashboard installer.
"""
import os
import subprocess
import tempfile
import shutil


def println(msg=""):
    """Print message with flush (imported from utils to avoid circular import)."""
    import sys
    print(msg)
    sys.stdout.flush()


def run_subprocess(cmd):
    """Run a subprocess command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def ssh_run(remote, remote_cmd):
    """Run a command on remote host via SSH."""
    return run_subprocess(["ssh", remote, remote_cmd])


def scp_copy(local_path, remote, remote_path):
    """Copy a file to remote host via SCP."""
    return run_subprocess(["scp", local_path, f"{remote}:{remote_path}"])


def remote_read_file(remote, path):
    """
    Read a remote file and return its contents as text. Returns None on failure.
    """
    code, out, err = ssh_run(remote, f"cat '{path}'")
    if code != 0:
        println(f"Failed to read remote file {path}: {err or out}")
        return None
    return out


def remote_find_printer_data(remote):
    """
    Try to find printer_data dirs on remote host.
    """
    code, home_out, err = ssh_run(remote, "echo $HOME")
    if code != 0 or not home_out:
        println(f"Could not determine remote HOME: {err}")
        return []

    home = home_out.strip()
    find_cmd = f'find "{home}" -maxdepth 5 -type d -name printer_data 2>/dev/null'
    code, out, err = ssh_run(remote, find_cmd)
    if code != 0:
        println(f"Remote find command failed: {err}")
        return []

    candidates = [line.strip() for line in out.splitlines() if line.strip()]
    return candidates


def remote_write_file(remote, path, content, mode=0o644):
    """
    Write small file to remote using scp via a temp local file, then chmod it.
    """
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
    except Exception as e:
        println(f"Failed to create temp file: {e}")
        return False

    try:
        code, out, err = scp_copy(tmp_path, remote, path)
        if code != 0:
            println(f"SCP to {remote}:{path} failed: {err}")
            return False

        code, out, err = ssh_run(remote, f"chmod {oct(mode)[2:]} '{path}'")
        if code != 0:
            println(f"chmod on remote failed: {err}")
        return True
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def remote_append_line_if_missing(remote, file_path, line):
    """
    Ensure a line is present at the top of a remote file.
    """
    code, out, err = ssh_run(remote, f"test -f '{file_path}' && echo EXISTS || echo MISSING")
    if code != 0:
        println(f"Failed to check remote file {file_path}: {err}")
        return False

    exists = (out.strip() == "EXISTS")
    if not exists:
        create_cmd = f"echo '{line}' > '{file_path}'"
        code, out2, err2 = ssh_run(remote, create_cmd)
        if code != 0:
            println(f"Failed to create {file_path}: {err2}")
            return False
        return True

    grep_cmd = f"grep -F '{line}' '{file_path}' >/dev/null 2>&1 && echo PRESENT || echo ABSENT"
    code, out2, err2 = ssh_run(remote, grep_cmd)
    if code != 0:
        println(f"Failed to grep remote file {file_path}: {err2}")
        return False
    if out2.strip() == "PRESENT":
        println(f"Line already present in {file_path}, not adding again.")
        return True

    cmd = (
        "tmpfile=$(mktemp) && "
        f"printf '%s\\n' '{line}' > \"$tmpfile\" && "
        f"cat '{file_path}' >> \"$tmpfile\" && "
        f"mv \"$tmpfile\" '{file_path}'"
    )
    code, out3, err3 = ssh_run(remote, cmd)
    if code != 0:
        println(f"Failed to prepend to {file_path}: {err3}")
        return False

    return True


def ensure_local_ssh_key():
    """
    Ensure we have an SSH key at ~/.ssh/id_ed25519.pub.
    Generate one with no passphrase if missing.
    """
    ssh_dir = os.path.expanduser("~/.ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    key_path = os.path.join(ssh_dir, "id_ed25519")
    pub_path = key_path + ".pub"

    if os.path.exists(pub_path):
        return pub_path

    println("No SSH key found at ~/.ssh/id_ed25519.pub, generating a new ed25519 key (no passphrase)...")
    code, out, err = run_subprocess(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path])
    if code != 0:
        println(f"Failed to generate SSH key: {err}")
        return None
    return pub_path


def setup_ssh_key_for_remote(remote):
    """
    Use ssh-copy-id to install our public key on the remote host.
    """
    if not shutil.which("ssh-copy-id"):
        println("ssh-copy-id is not installed, cannot automatically install SSH keys.")
        println("You can install SSH keys manually later if you like.")
        return False

    pub_path = ensure_local_ssh_key()
    if not pub_path:
        return False

    println(f"Setting up SSH key-based login for {remote}.")
    println("You may be prompted once for the SSH password and to confirm the host key.\n")
    code, out, err = run_subprocess(["ssh-copy-id", "-i", pub_path, remote])
    if code != 0:
        println(f"ssh-copy-id failed: {err or out}")
        return False

    println("SSH key installed for remote host; subsequent ssh/scp should not ask for a password.")
    return True

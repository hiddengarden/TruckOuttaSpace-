#!/usr/bin/env python3
"""Environment discovery for deploying the `agency` stack (+ Ollama, ComfyUI,
Postiz, a WordPress staging site, Paperless-ngx, Khoj, n8n) on this Fedora
Silverblue host via rootless Podman + Quadlet.

RUN THIS ON YOUR ACTUAL MACHINE, not in any container/VM -- it needs to see
your real Podman state, firewalld config, GPU devices, and disks.

Safe to paste the full output back into chat: it never prints secret VALUES,
only whether a variable looks set (and from which file). It also performs a
LIVE network test: it creates a throwaway Podman network and two short-lived
busybox containers to empirically check container-to-container DNS
resolution and outbound reachability, then removes them. Pass
--skip-network-test to skip that part (e.g. if you'd rather not have it pull
an image or create anything, even temporarily).

Stdlib only. No pip install needed.
"""
import argparse
import getpass
import os
import shutil
import subprocess
from pathlib import Path

SECTION = "=" * 78


def header(title: str) -> None:
    print(f"\n{SECTION}\n{title}\n{SECTION}")


def run(cmd: list[str], timeout: float = 10.0) -> tuple[str, str, int]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        return "", f"{cmd[0]}: command not found", 127
    except subprocess.TimeoutExpired:
        return "", f"{cmd[0]}: timed out after {timeout}s", -1


def show(cmd: list[str], timeout: float = 10.0) -> tuple[str, int]:
    out, err, code = run(cmd, timeout)
    print(f"\n$ {' '.join(cmd)}   (exit {code})")
    if out:
        print(out)
    if err:
        print(f"[stderr] {err}")
    return out, code


def section_system() -> None:
    header("SYSTEM")
    show(["cat", "/etc/os-release"])
    show(["uname", "-a"])
    show(["getenforce"])
    show(["loginctl", "show-user", getpass.getuser(), "--property=Linger"])


def section_podman() -> None:
    header("PODMAN")
    show(["podman", "--version"])
    show(["podman", "info", "--format", "{{.Host.Security.Rootless}} rootless | netBackend={{.Host.NetworkBackend}} | cgroupVersion={{.Host.CgroupsVersion}}"])
    out, _ = show(["podman", "network", "ls", "--format", "{{.Name}} driver={{.Driver}}"])
    for line in out.splitlines():
        name = line.split()[0] if line.split() else ""
        if name:
            show(["podman", "network", "inspect", name])
    show(["podman", "ps", "-a", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Networks}}"])
    for d in [Path.home() / ".config/containers/systemd", Path("/etc/containers/systemd")]:
        print(f"\nQuadlet dir {d}: ", end="")
        print(sorted(p.name for p in d.iterdir()) if d.is_dir() else "(does not exist)")


def section_firewalld() -> None:
    header("FIREWALLD")
    out, code = show(["firewall-cmd", "--state"])
    if code != 0:
        print("firewalld not running/installed -- skip zone checks.")
        return
    zones_out, _ = show(["firewall-cmd", "--get-active-zones"])
    for line in zones_out.splitlines():
        if line and not line.startswith(" ") and not line.startswith("\t"):
            zone = line.strip()
            show(["firewall-cmd", f"--zone={zone}", "--list-all"])


def section_live_network_test(skip: bool) -> None:
    header("LIVE NETWORK TEST (empirical aardvark-dns + outbound check)")
    if skip:
        print("Skipped (--skip-network-test).")
        return

    net = "agency-diag-net"
    a, b = "agency-diag-a", "agency-diag-b"
    image = "docker.io/library/busybox:latest"

    run(["podman", "rm", "-f", a, b])
    run(["podman", "network", "rm", "-f", net])

    print("Creating a throwaway user-defined network...")
    out, err, code = run(["podman", "network", "create", net], timeout=15)
    if code != 0:
        print(f"FAILED to create network: {err or out}")
        print("-> Can't run the live test; this alone is diagnostic (netavark/permissions issue).")
        return
    print("OK")

    print(f"\nPulling {image} if not already present (needed for the test)...")
    out, err, code = run(["podman", "pull", image], timeout=90)
    if code != 0:
        print(f"FAILED to pull test image: {err or out}")
        print("-> Outbound registry access is broken from the host itself, separate from container networking.")
        run(["podman", "network", "rm", "-f", net])
        return
    print("OK")

    run(["podman", "run", "-d", "--name", a, "--network", net, image, "sleep", "120"], timeout=30)
    run(["podman", "run", "-d", "--name", b, "--network", net, image, "sleep", "120"], timeout=30)

    print(f"\n--- Container-to-container DNS: can '{b}' resolve '{a}' by name? ---")
    out, err, code = run(["podman", "exec", b, "nslookup", a], timeout=10)
    print(out or err)
    print("RESULT: PASS" if code == 0 else "RESULT: FAIL (aardvark-dns not resolving container names)")

    print(f"\n--- Outbound from '{a}': can it reach the public internet? ---")
    out, err, code = run(["podman", "exec", a, "wget", "-q", "-T", "5", "-O", "-", "http://example.com"], timeout=10)
    print("RESULT: PASS (outbound works)" if code == 0 else f"RESULT: FAIL -- {err or 'no output'}")

    print("\nCleaning up test containers/network...")
    run(["podman", "rm", "-f", a, b])
    run(["podman", "network", "rm", "-f", net])
    print("Done.")


def section_gpu() -> None:
    header("GPU / ROCm")
    show(["groups"])
    for dev in ["/dev/kfd", "/dev/dri"]:
        exists = os.path.exists(dev)
        print(f"\n{dev} exists: {exists}")
        if exists and os.path.isdir(dev):
            print(f"  contents: {os.listdir(dev)}")
        elif exists:
            st = os.stat(dev)
            print(f"  mode={oct(st.st_mode)} uid={st.st_uid} gid={st.st_gid}")
    if shutil.which("rocminfo"):
        out, _, _ = run(["rocminfo"], timeout=20)
        lines = out.splitlines()
        print("\n$ rocminfo (first 40 lines)")
        print("\n".join(lines[:40]))
        if len(lines) > 40:
            print(f"... ({len(lines) - 40} more lines truncated)")
    else:
        print("\nrocminfo: not found on PATH")
    show(["rocm-smi"], timeout=15)


def section_ports() -> None:
    header("LISTENING PORTS")
    known = {
        3000: "Postiz", 5432: "Postgres", 6379: "Redis", 11434: "Ollama",
        8188: "ComfyUI", 8000: "Paperless-ngx (common)", 8010: "Paperless-ngx (alt)",
        42110: "Khoj", 5678: "n8n", 8080: "WordPress (common alt)", 80: "HTTP", 443: "HTTPS",
    }
    out, err, code = run(["ss", "-tlnp"])
    print(out or err)
    print("\nKnown ports this stack cares about (check the listing above for collisions):")
    for port, name in sorted(known.items()):
        print(f"  {port}: {name}")


def section_disks() -> None:
    header("DISKS / MOUNTS (pick non-OS-drive locations for volumes here)")
    show(["lsblk", "-f"])
    show(["df", "-hT"])


def _search_roots() -> list[Path]:
    roots = [Path.home()]
    for base in ["/mnt", "/run/media", "/media", "/var/mnt"]:
        p = Path(base)
        if p.is_dir():
            try:
                roots.extend(x for x in p.iterdir() if x.is_dir())
            except PermissionError:
                pass
    return roots


def section_candidate_dirs() -> None:
    header("CANDIDATE EXISTING SERVICE DIRECTORIES (name-matched, with size)")
    patterns = ["ollama", "comfyui", "paperless", "khoj", "n8n", "postiz", "wordpress", "agency"]
    roots = _search_roots()
    print(f"Searching top-level entries under: {', '.join(str(r) for r in roots)}")
    found = []
    for root in roots:
        try:
            for entry in root.iterdir():
                if entry.is_dir() and any(p in entry.name.lower() for p in patterns):
                    found.append(entry)
        except PermissionError:
            continue
    if not found:
        print("(none found by name match -- that's fine, just means nothing to report here)")
    for path in found:
        out, _, _ = run(["du", "-sh", str(path)], timeout=30)
        print(f"  {out or path}")

    header("OBSIDIAN VAULT CANDIDATES (folders containing a .obsidian subfolder)")
    any_found = False
    for root in roots:
        out, err, code = run(["find", str(root), "-maxdepth", "4", "-type", "d", "-name", ".obsidian"], timeout=20)
        if out:
            print(out)
            any_found = True
    if not any_found:
        print("(none found within 4 directory levels of the search roots above)")


def section_env_files() -> None:
    header("EXISTING .env / CONFIG FILES (paths + variable NAMES only -- values redacted)")
    roots = _search_roots()
    candidates: set[str] = set()
    for root in roots:
        out, _, _ = run(["find", str(root), "-maxdepth", "3", "-iname", ".env*"], timeout=15)
        candidates.update(line for line in out.splitlines() if line)

    if not candidates:
        print("(no .env* files found within 3 directory levels of the search roots above)")

    for path_str in sorted(candidates):
        print(f"\n{path_str}:")
        try:
            for line in Path(path_str).read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0]
                print(f"  {key}=<redacted>")
        except Exception as exc:
            print(f"  (could not read: {exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-network-test",
        action="store_true",
        help="Skip the live create-network/containers/pull-image test",
    )
    args = parser.parse_args()

    section_system()
    section_podman()
    section_firewalld()
    section_live_network_test(args.skip_network_test)
    section_gpu()
    section_ports()
    section_disks()
    section_candidate_dirs()
    section_env_files()

    header("DONE -- copy everything above this line and send it back")


if __name__ == "__main__":
    main()

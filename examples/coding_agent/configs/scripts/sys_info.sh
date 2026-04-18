#!/bin/bash
# Get system and terminal information
# @param section:string:Info section: os, env, cwd, disk, memory, all (default: all)

section="${section:-all}"

show_os() {
    echo "=== OS ==="
    uname -a
    echo ""
}

show_env() {
    echo "=== Environment ==="
    echo "USER: $(whoami)"
    echo "HOME: $HOME"
    echo "SHELL: $SHELL"
    echo "PATH: $(echo $PATH | tr ':' '\n' | head -5)..."
    echo "LANG: ${LANG:-not set}"
    echo "TERM: ${TERM:-not set}"
    echo ""
}

show_cwd() {
    echo "=== Working Directory ==="
    echo "CWD: $(pwd)"
    echo ""
}

show_disk() {
    echo "=== Disk Usage ==="
    df -h . 2>/dev/null || echo "df not available"
    echo ""
}

show_memory() {
    echo "=== Memory ==="
    free -h 2>/dev/null || echo "free not available"
    echo ""
}

case "$section" in
    os)      show_os ;;
    env)     show_env ;;
    cwd)     show_cwd ;;
    disk)    show_disk ;;
    memory)  show_memory ;;
    all)     show_os; show_env; show_cwd; show_disk; show_memory ;;
    *)       echo "Unknown section: $section" >&2; exit 1 ;;
esac

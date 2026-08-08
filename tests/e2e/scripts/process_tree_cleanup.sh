#!/usr/bin/env bash

# Shared bounded teardown for E2E runners. Signal descendants before their
# parent so a Bash process blocked on command substitution can run its trap.

_zf_e2e_signal_process_tree() {
  local pid="$1"
  local signal_name="$2"
  local child
  while IFS= read -r child; do
    [[ -n "$child" ]] || continue
    _zf_e2e_signal_process_tree "$child" "$signal_name"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -s "$signal_name" "$pid" 2>/dev/null || true
}

zf_e2e_terminate_process_tree() {
  local pid="${1:-}"
  local state=""
  local attempt
  [[ -n "$pid" ]] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return 0
  fi

  _zf_e2e_signal_process_tree "$pid" TERM
  for attempt in $(seq 1 20); do
    state="$(ps -o stat= -p "$pid" 2>/dev/null | awk '{print $1}' || true)"
    if [[ -z "$state" || "$state" == Z* ]]; then
      break
    fi
    sleep 0.2
  done
  state="$(ps -o stat= -p "$pid" 2>/dev/null | awk '{print $1}' || true)"
  if [[ -n "$state" && "$state" != Z* ]]; then
    _zf_e2e_signal_process_tree "$pid" KILL
  fi
  wait "$pid" 2>/dev/null || true
}

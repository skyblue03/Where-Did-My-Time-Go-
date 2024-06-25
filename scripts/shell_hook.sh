# Add to ~/.bashrc or ~/.zshrc
timetrace_preexec() {
  export TIMETRACE_CMD="$1"
  export TIMETRACE_START=$(date +%s)
}
timetrace_precmd() {
  if [ -n "$TIMETRACE_CMD" ]; then
    END=$(date +%s)
    timetrace run -- "$TIMETRACE_CMD"
    unset TIMETRACE_CMD TIMETRACE_START
  fi
}

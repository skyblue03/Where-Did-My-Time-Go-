# Add to PowerShell profile
Register-EngineEvent PowerShell.OnCommandExecuted -Action {
  timetrace run -- $EventArgs.CommandLine
}

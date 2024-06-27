# Shell integrations (auto-tracking)

TimeTrace can auto-track commands you run in your shell.

## Bash / Zsh
Add this to your shell rc file:

```sh
eval "$(timetrace init bash)"
```

Optional:
- `export TIMETRACE_PROJECT="MyProject"`
- `export TIMETRACE_TAG="course"`

## PowerShell
Add this to your PowerShell profile (preserves your existing prompt):

```powershell
Invoke-Expression (& timetrace init powershell)
```

Optional:
- `$env:TIMETRACE_PROJECT="MyProject"`
- `$env:TIMETRACE_TAG="course"`

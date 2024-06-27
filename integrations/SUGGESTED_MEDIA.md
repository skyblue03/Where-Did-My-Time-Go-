# Suggested screenshots / GIFs for the README

Keep these short (3-8 seconds per GIF) and focus on outcomes.

## 1) Auto-tracking enabled (PowerShell)
- Show enabling: `Invoke-Expression (& timetrace init powershell)`
- Run 2-3 commands normally (no `timetrace run`)
- Then run `timetrace report --today` and it shows tracked time

## 2) Ignore rules
- `timetrace ignore list`
- Add an ignore: `timetrace ignore add-prefix code`
- Run `code .` then show it doesn't appear in `timetrace list`

## 3) Sessions
- `timetrace session start handzplay-loop`
- Run a test/build command
- `timetrace session stop`
- `timetrace report --today --session 1`

## 4) Prettier report
- `timetrace report --last 7`
- Make sure you have a mix of categories so bar charts look good

Tips:
- Use a fixed-width terminal font.
- Crop to the relevant area.
- Prefer dark theme for contrast (optional).

# Clickvert

Right-click a file in Windows File Explorer and convert it to another format.
Free, open source (MIT), and powered by [FFmpeg](https://ffmpeg.org/).

> **Status:** early development. Works from the terminal and from File
> Explorer's right-click menu.

## Supported conversions

| From | To  | Notes |
|------|-----|-------|
| MP4  | GIF | 12 fps, up to 480 px wide (smaller videos are not enlarged) |
| MP4  | MP3 | Extracts the audio track |
| GIF  | MP4 | H.264, plays everywhere |

The converted file is saved **next to the original**. The original is never
modified, and existing files are never overwritten: if `clip.gif` already
exists, the new file is named `clip (1).gif`.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- FFmpeg: `winget install Gyan.FFmpeg`

Clickvert finds FFmpeg on your `PATH`. To use a specific copy, set the
`CLICKVERT_FFMPEG` environment variable to the full path of `ffmpeg.exe`.

## Right-click menu

Add Clickvert to File Explorer's right-click menu (no admin rights needed):

```powershell
python src\clickvert install --dry-run   # preview the registry changes
python src\clickvert install             # add the menu
python src\clickvert uninstall           # remove it again
```

Then right-click an MP4 or GIF file and choose **Clickvert**. Only conversions
that work for that file type are listed. On **Windows 11**, choose
**Show more options** first, or hold **Shift** while right-clicking.

What `install` does:

- It writes only under `HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\<.ext>\shell\Clickvert`,
  for your Windows account only.
- It does not change which app opens your files, and it does not change Windows'
  right-click menu settings.
- The menu runs Clickvert from this folder. If you move the folder or change
  Python versions, run `install` again.

`uninstall` deletes those `Clickvert` keys and any parent keys it leaves empty.
It never touches other apps' entries.

## Usage from the terminal

From the project folder:

```powershell
$env:PYTHONPATH = "src"
python -m clickvert convert --to gif "C:\Videos\holiday.mp4"
python -m clickvert convert --to gif --window "C:\Videos\holiday.mp4"
python -m clickvert formats
```

## How a conversion works

1. Clickvert checks that the file exists, can be read, and has a supported type.
2. It creates an empty placeholder next to the original, for example
   `clip.clickvert-tmp-1a2b3c4d.gif`. FFmpeg writes into this file.
3. When FFmpeg finishes successfully, the placeholder is renamed to
   `clip.gif` (or `clip (1).gif` if that name is taken).
4. If anything fails, or the conversion is cancelled, the placeholder is deleted.

If Windows itself is shut down mid-conversion, a `.clickvert-tmp-` file may
remain. It's safe to delete.

## Project layout

| File | Purpose |
|------|---------|
| `src/clickvert/conversions.py` | The table of supported conversions and their FFmpeg settings |
| `src/clickvert/converter.py` | Runs one conversion start to finish, with progress and cancel |
| `src/clickvert/ffmpeg.py` | Finds FFmpeg and runs it safely |
| `src/clickvert/paths.py` | Input checks, temp files, and no-overwrite naming |
| `src/clickvert/errors.py` | Error types with plain-language messages |
| `src/clickvert/progress_window.py` | The progress window (tkinter): progress bar, Cancel, result |
| `src/clickvert/shell_integration.py` | The right-click menu: registry install / uninstall / dry run |
| `src/clickvert/cli.py` | The `python -m clickvert` command |

## Known limitations

- Paths longer than 260 characters need Windows' long path support turned on
  (`LongPathsEnabled`, on by default on many Windows 11 PCs).
- Very long videos take a while to become GIFs. GIF is an inefficient format.
- Selecting several files converts each one in its own window. Windows shows
  the menu for at most 15 selected files.

## Running the tests

```powershell
python -m unittest -v
```

The end-to-end tests generate tiny sample videos with FFmpeg as they run. If
FFmpeg isn't installed, those tests are skipped.

## License

MIT. See [LICENSE](LICENSE). FFmpeg is a separate project with its own license
and is not included with Clickvert.

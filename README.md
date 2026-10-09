# Clickvert

Clickvert is a small project I'm working on that lets you convert files by right clicking them in Windows File Explorer instead of having to open a separate program.

It uses Python and FFmpeg to convert files.

## What works so far

- MP4 to GIF
- MP4 to MP3
- GIF to MP4
- Right click menu in File Explorer
- Progress bar and cancel button

The original file stays the same and the converted file saves in the same folder.

## How to use

You'll need Python 3.10+ and FFmpeg installed.

To install FFmpeg:

```powershell
winget install Gyan.FFmpeg
```

To add Clickvert to the right click menu, run this in the project folder:

```powershell
python src\clickvert install
```

On Windows 11, right click a file, press **Show more options**, then select Clickvert.

To remove it:

```powershell
python src\clickvert uninstall
```

## Stuff I want to add

- Compress videos and GIFs for Discord
- Trim videos without losing quality
- Support more file formats
- Make an installer so you don't need Python

## License

MIT

# Advance Manager RS Utility User Guide

## Overview
The `asura.py` utility now focuses on a single graphical workflow for inspecting and modifying textures inside RS archives created for the Asura engine. Launching the script opens the management interface directly, allowing you to browse the resource tree, preview images through Pillow, and prepare replacements without dropping back to the command line.

## Highlighted improvements
- **Modernized interface** with `ttk` widgets, themed controls, and a layout identical to the classic version so existing users feel at home.
- **Smart format warnings:** if the replacement does not match the expected compression (BC/DXT) or alpha channel, the program displays an alert before applying the change.
- **Optional backups:** the first time you import a texture, you are asked whether automatic backups should be created. You can toggle the preference through the "Create automatic backup" checkbox.
- **Improved bulk export:** select entire folders in the tree and export all contained images in one step.
- **Drag and drop:** drop DDS/PNG/TGA textures or WAV audio clips directly on the window to import them without opening the dialog; skip dropping `manifest.json` files because the tool expects the replacement payload itself.
- **Dependency health checks:** the GUI now warns at launch when Pillow, ImageTk, tkinterdnd2, NumPy, or sounddevice are missing so you know exactly which features are unavailable.
- **Guided dependency installation:** a new Windows script automates the installation of Pillow and TkinterDnD2, simplifying package distribution.

## Requirements
- Python 3.10 or newer.
- Tkinter (included with most standard Python distributions).
- [Pillow](https://python-pillow.org/) for texture previews.
- [TkinterDnD2](https://pypi.org/project/TkinterDnD2/) to enable drag and drop on Windows.
- [`sounddevice`](https://python-sounddevice.readthedocs.io/) together with `numpy` enables the in-app audio preview controls.

You can install the dependencies manually with:

```bash
python -m pip install -r requirements.txt
```

If you only need to enable drag and drop (for example, when Python and Pillow are already configured), install `tkinterdnd2` directly with:

```bash
python -m pip install tkinterdnd2
```

On Windows the repository also includes `windows_install.bat`, which optionally accepts the path to your Python interpreter:

```bat
windows_install.bat
windows_install.bat C:\Python311\python.exe
```

## Using the interface
1. Run `python asura.py`. If Tkinter is available the main window will open.
2. Click **Open Archive** and choose an `.asr/.pc/.es/.en/.fr/.de/.sp/.it/.ge/.pl` file. The tree will list every detected resource, including audio banks.
3. Select one or more entries (or entire folders) and click **Export Selected** to save them.
   - Texture exports mirror the legacy workflow and still generate `.rsmeta` companions for faster round-tripping.
   - Audio exports now output `.wav` files together with `.rsmeta` descriptors that capture sample rate, loop points, and language routing.
4. To import a replacement:
   - For textures, select the entry in the tree and click **Import Replacement**, use **Import Folder** to process an entire export directory that still contains the generated `.rsmeta` files, or drag the image (`.dds/.png/.tga`) directly onto the window.
   - For audio, highlight the sound entry and supply a `.wav` file. Keep the original `.rsmeta` in the same folder so the importer can restore metadata automatically. Avoid dropping `manifest.json`—it is only used for repacking.
   - The manager checks the format automatically. Texture validation still verifies compression and alpha, and audio imports confirm channel count, duration, and cue markers before accepting the file.
   - Enable the backup checkbox if you want to preserve the original asset inside the `rsmanager_backups` folder next to the imported file.
5. Review the preview panel before committing changes:
   - Texture previews continue to use Pillow and expose the channel toggle buttons.
   - Audio entries show **Play**, **Pause**, **Stop**, and **Loop** controls. Install the optional audio backend (sounddevice + numpy) so these buttons become active.
   - The waveform overlay highlights loop start/end markers plus cue regions captured in the `.rsmeta` file.
6. When you are satisfied with the replacement queue, generate a patch with **Create Patch** or write a full archive with **Save Archive As**.
7. Use **Load Modification Log** to re-import a previously generated `.replacements.json`.

## Practical tips
- Use the search bar to filter textures quickly; the previous selection is kept to speed up the workflow.
- After dragging a file, check the bottom status message to confirm the replacement was queued.
- Backups include a timestamp to avoid accidental overwrites.
- If you want to see the backup prompt again, clear and re-enable the checkbox; the preference takes effect immediately.
- When working with audio, audition the entry inside the GUI to confirm loop points before you rebuild the archive. If the playback buttons remain disabled, install the optional sounddevice + numpy backend and restart the application.

With these improvements the tool delivers a faster editing loop: open ➜ export ➜ modify ➜ verify in the GUI ➜ apply or build a patch. Enjoy the updated texture manager!

## Command-line example: updating a speech clip

The helper functions in `asura.py` handle non-texture resources the same way they treat images. The following workflow replaces a single `.wav` entry inside `M01_Colony.sp` and bundles the modification into a standalone patch:

1. Extract the archive and inspect the manifest:

   ```bash
   python - <<'PY'
   from pathlib import Path
   from asura import extract_archive

   extract_archive(Path('M01_Colony.sp'), Path('speech_dump'))
   manifest = Path('speech_dump/manifest.json').read_text()
   print('Entry count:', manifest.count('"relative_path"'))
   PY
   ```

   The manifest records each entry's `relative_path`, `layout`, `chunk_offset`, `chunk_size`, and `header_version`, so you can verify how the payload is wrapped before editing it.

2. Replace the target clip by dropping a new `.wav` into the extraction directory. Keep the same filename (for example `speech_dump/Sounds/Speech/en/AI/Male_Marine_01/AGGRESSIVE_TO_ALERT_MAR01_08.wav`).

3. Write a minimal patch archive that only contains the modified audio:

   ```bash
   python - <<'PY'
   from pathlib import Path
   from asura import write_patch_archive

   write_patch_archive(
       Path('M01_Colony.sp'),
       Path('speech_dump/manifest.json'),
       Path('speech_dump'),
       Path('M01_Colony_patch.sp'),
   )
   PY
   ```

   The helper scans `speech_dump` for files whose bytes differ from the pristine copy, then uses the manifest's offsets to build a patch compatible with the original container.

4. To create a full rebuilt archive instead, call `repack_archive(Path('speech_dump'), Path('M01_Colony_repacked.sp'))`. The function reuses `chunk_offset` and `header_data_span` metadata from the manifest to keep the rebuilt file byte-aligned with the original.

This same flow works for materials (`.MARE`), particle definitions (`.FXPT`/`.FXET`), scripts, or any other RS-managed resource listed in the manifest.

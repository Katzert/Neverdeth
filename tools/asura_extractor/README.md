# Asura Engine Extractor

Asura Engine Extractor is a Python utility focused on working with RS archives used by the Advance Manager toolchain. It streamlines both command-line and graphical workflows so modders can inspect, extract, and repack game assets without reverse-engineering the archive format themselves.

## Project purpose
- Provide a cross-platform toolkit for extracting and rebuilding `.asr`, `.pc`, `.es`, `.en`, `.fr`, `.de`, `.sp`, `.it`, `.ge`, and `.pl` archives that follow the RSFL container layout.
- Offer a modern Tkinter-based GUI for browsing textures, previewing assets, and managing replacements with quality checks.
- Lay the groundwork for the upcoming LST workflow, ensuring future patches can bundle archive edits together with scene configuration updates.

## Key features
- **Unified extractor and repacker:** Load existing archives, decompress when needed, and emit a `manifest.json` that captures all metadata required for lossless rebuilds.
- **Texture-first GUI:** Launch a desktop interface that mirrors the classic tool while adding drag-and-drop imports, filtered search, bulk export, and compression validation warnings.
- **Dependency self-checks:** At startup the tool prints a summary of missing optional packages, offers to install the pip-managed ones automatically, and reminds you which features (image previews, audio playback, drag-and-drop) stay disabled until you add them.
- **Smart backups:** Enable automatic backups the first time you import a replacement, keeping originals safe inside a timestamped folder.
- **Mass export and import queues:** Select folders or multiple entries and export them in one click. Import replacements either via dialogs or drag-and-drop.
- **LST workflow ready:** The project roadmap introduces LST patching to synchronize archive contents with game scene descriptors so the GUI can author complete mods once the workflow lands.

## Latest updates
- **PNG replacements auto-convert through ImageMagick:** Dropping a `.png` now shells out to ImageMagick so the texture is transcoded to `.dds` using the compression and mipmap targets recorded in its `.rsmeta`; the GUI surfaces command details and halts the import if ImageMagick is missing.【F:asura_core/gui/app.py†L944-L1014】【F:tests/test_metadata_resolution.py†L152-L214】
- **Metadata-aware replacements respect every list view:** The GUI resolves `.rsmeta` lookups against the filtered, image-only, and full entry collections so imports succeed even when search filters are active.【F:tests/test_metadata_resolution.py†L19-L76】
- **Clearer archive mismatch warnings:** When metadata cannot be matched to an entry, the dialog now states that the target could not be located in the opened archive, reducing guesswork when sidecars point at obsolete paths.【F:tests/test_metadata_resolution.py†L31-L44】
- **Native `.gui` archive support:** Open HUD/layout packages directly from the file picker alongside the traditional `.asr`, `.pc`, and regional variants—no extra flags required.【F:asura.py†L4774-L4799】
- **WAV workflows covered by regression tests:** Audio exports generate `.wav` plus `.rsmeta` companions, and imports validate channel, sample-rate, and metadata consistency through dedicated unit coverage.【F:tests/test_audio_support.py†L14-L83】

## Installation requirements
- Python 3.10 or newer
- Tkinter (bundled with most standard Python distributions)
- [Pillow](https://python-pillow.org/) for image previews
- [TkinterDnD2](https://pypi.org/project/TkinterDnD2/) to enable drag-and-drop on Windows
- [sounddevice](https://python-sounddevice.readthedocs.io/) + [NumPy](https://numpy.org/) to enable the built-in audio preview controls. Install them via `pip install sounddevice numpy`.
- [ImageMagick](https://imagemagick.org/) available as `magick` or `convert` in `PATH` so PNG imports can be transcoded to `.dds` with preserved compression and mipmap metadata.【F:asura_core/gui/app.py†L944-L1014】

Install dependencies with:

```bash
python -m pip install -r requirements.txt
```

### Installing TkinterDnD2

Drag-and-drop support relies on the [tkinterdnd2](https://pypi.org/project/TkinterDnD2/) package, which is not bundled with the
standard library. If you only need to install this dependency (for example when Python is already configured and Pillow is
available) run:

```bash
python -m pip install tkinterdnd2
```

On Windows make sure you are using the same Python interpreter that launches the GUI (for example `py -3.11 -m pip install
tkinterdnd2`).

On Windows you can also run the helper script (optionally passing your Python path):

```bat
windows_install.bat
windows_install.bat C:\\Path\\To\\python.exe
```

## Usage
### Command line
Use `asura.py`'s helper functions from Python to mirror the legacy `extract`/`repack` commands:

```bash
python - <<'PY'
from pathlib import Path
from asura import extract_archive, repack_archive

extract_archive(Path("path/to/archive.asr"), Path("output_dir"))
repack_archive(Path("output_dir"), Path("output_archive.asr"))
PY
```

The extractor writes a `manifest.json` and stores a copy of the original archive alongside the exported files so repacking can reuse unchanged payloads.

#### Editing non-texture resources

1. Run `extract_archive` on the target `.pc/.sp/.asr` file. Every entry appears in `manifest.json` with its `relative_path`, `layout`, and RSCL metadata (`chunk_offset`, `chunk_size`, `header_version`).
2. Modify the exported payloads in-place. Materials, particle templates, scripts, or audio clips can be edited directly—the manifest preserves the exact offsets required to splice new data back into the container.
3. When ready to distribute the change, either:
   - Call `write_patch_archive(Path("Colony.pc"), extraction_dir / "Colony.pc", extraction_dir, Path("Colony_patch.pc"))` to emit a minimal patch (the helper scans the directory for modified payloads), or
   - Invoke `repack_archive(extraction_dir, Path("updated_archive.pc"))` to build a full replacement archive.

Because the helpers reuse the `chunk_offset` and `header_data_span` fields recorded during extraction, you can update arbitrarily complex resources without reverse-engineering the surrounding RSCF wrapper. The rebuilt archive keeps the original header intact, so you do not need an empty `.asr` template—new payloads of any size are appended safely based on the manifest metadata.【F:README.md†L79-L106】

### Graphical interface
1. Launch the GUI with `python asura.py`. The legacy `--gui` flag remains optional for backwards compatibility (`python asura.py --gui`).
2. Click **Open Archive** and select an `.asr`, `.gui`, `.pc`, `.es`, `.en`, `.fr`, `.de`, `.sp`, `.it`, `.ge`, or `.pl` file.
3. Browse the tree to inspect textures, then use **Export Selected** (or right-click the tree to export the current selection as `.dds`/`.png` while preserving alpha) to save assets—the tool generates `.rsmeta` helpers for faster re-imports.
4. Import replacements with **Import Replacement** for a single file, **Import Folder** to queue every exported asset that still has its `.rsmeta` companion, or by dropping the replacement texture (`.dds/.png/.tga`) or audio clip (`.wav`) directly into the window. Compression and alpha mismatches trigger a warning before changes apply, and PNG imports demand ImageMagick so the tool can convert them to `.dds` while honouring the `.rsmeta` compression settings.【F:asura_core/gui/app.py†L944-L1014】【F:tests/test_texture_export.py†L54-L140】 Skip dropping the generated `manifest.json`—use the entry tree to target the resource you want to replace instead.
5. Toggle **Create automatic backup** if you want the tool to duplicate the original asset the first time you replace it.
6. Generate distribution files with **Create Patch** or rebuild the full archive through **Save Archive As**.
7. Reload previous work using **Load Modification Log** with a `.replacements.json` file.

Full saves continue to rebuild the container from the manifest, preserving the original header and offsets so brand-new archives can exceed the source file without requiring a blank template.【F:README.md†L79-L106】

### Audio workflows
#### Export `.wav` audio
1. Select an audio entry inside the archive tree and click **Export Selected**.
2. Save the extracted asset to your workspace; `asura.py` emits a `.wav` file alongside an `.rsmeta` companion that records the original archive metadata.
3. Keep the `.rsmeta` file next to the exported audio so loop points, markers, and language routing survive later imports.

#### Import `.wav` audio
1. Prepare a replacement `.wav` file that matches the target channel count and sample rate reported in the `.rsmeta` metadata.
2. Highlight the audio entry in the GUI, then choose **Import Replacement** (or drag and drop the `.wav` onto the window).
3. Confirm the import summary: `asura.py` validates duration, sample format, and cue positions before queuing the replacement.
4. Commit the change with **Save Archive As** or include it in a patch created through **Create Patch**.

#### Metadata preservation
- Do not delete the exported `.rsmeta` file. `asura.py` reuses it to restore loop points, subtitles, and per-language routing when you import your edited `.wav`.
- When the `.rsmeta` file is present, repacks carry forward the archive identifiers so audio banks keep their original ordering and references.
- If you must regenerate metadata, re-export the entry first to capture a fresh `.rsmeta` snapshot before editing.

#### Audio preview
- The preview panel now exposes dedicated audio controls whenever you select a supported sound entry.
- Use **Play**, **Pause**, and **Stop** to audition the `.wav` directly inside the GUI; toggle **Loop** to repeat the segment while adjusting edits.
- Install the optional audio playback helpers (`sounddevice` + `numpy`) to enable the controls. Without them, the GUI keeps the buttons disabled and shows a reminder in the status bar.
- The waveform panel highlights loop start and end markers so you can confirm that metadata matches the exported `.rsmeta` before rebuilding the archive.

### Material editing quick reference
Materials (`.MARE`) and related response tables follow the same export-and-repack pattern documented above. The research bundle lists representative payloads—`colony_archive_overview.md` highlights dedicated “Material Response Texture” and shader tables extracted from `Colony.pc`, while the FX parameter documentation explains how grouped float values inside `.FXPT` records reference fallback textures such as `specialfx/marine_small_box_01.tga`. Use these manifests to map texture dependencies before wiring the values into GUI sliders or custom tooling.【F:research/colony_archive_overview.md†L13-L27】【F:research/avp2010_filetypes.md†L1-L32】

## Supplementary guides
- Read the full walkthrough in [`docs.md`](docs.md) for screenshots, workflow tips, and detailed explanations of every GUI control.
- The repository will ship an LST workflow primer alongside the new feature; keep an eye on release notes for that documentation update.

## Supported archive types
- RSFL archives stored as `.asr`, `.pc`, `.es`, `.en`, `.fr`, `.de`, `.sp`, `.it`, `.ge`, and `.pl` containers
- AsuraZlb and AsuraZbb compressed variants, which the tool transparently decompresses and repacks

## Roadmap: LST workflow
The upcoming LST workflow will allow you to generate scene configuration updates that accompany archive edits. Once released, the README will highlight the exact steps for building combined RS and LST patches from the GUI and CLI.

## Manual testing
- Generate a patch via `Create Patch` and add it to an existing list in `envs\Colony` to verify the path uses the detected folder prefix.
- Repeat the operation with an empty list located in another `envs` subfolder (for example, `envs\Training`) and confirm it uses the opened archive directory as the prefix before appending the patch name.


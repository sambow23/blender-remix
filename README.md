# Remix Toolkit for Blender

A Blender addon for importing and exporting RTX Remix USD files.
This can be used as a replacement to the official NVIDIA toolkit as it's able to import RTX Remix captures and export assets back in a NVIDIA Omniverse USD compatible format.

## Requirements
- A system running Windows (or use Wine on Linux)
- [Blender 4.0.2](https://download.blender.org/release/Blender4.0/blender-4.0.2-windows-x64.zip) (not verified to work on newer versions)
- [texconv from DirectXTex](https://github.com/microsoft/DirectXTex/releases/latest/download/texconv.exe)

## Installation

1. Download or clone this repository, extract to a folder
2. Place `texconv.exe` in `rtx_remix_importer/texconv/`
2. Install the addon in Blender:
   - Go to `Edit > Preferences > Add-ons`
   - Click `Install...` and select the `rtx_remix_importer` folder
   - Enable the "RTX Remix USD Importer" addon

## Usage

### Import Captures
- Use the **RTX Remix** panel in the 3D viewport sidebar (N-key)
- Select your game's capture folder under `Captures > Capture Folder`
- Import individual capture USD files or batch import multiple

### Project Management
1. Load a RTX Remix project: Set the path to your `mod.usda` file
2. Create or add sublayers for organizing your mod content
3. Set target sublayer for exports

### Export Assets
- Select objects in Blender
- Use the export buttons in the RTX Remix panel
- Assets are exported with the proper Remix material setup so they should work out of the box

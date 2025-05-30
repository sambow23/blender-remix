# Remix Toolkit for Blender
![image](https://github.com/user-attachments/assets/c20812f9-6efb-446a-9c86-e797bb90682e)

A Blender addon for importing and exporting RTX Remix USD files.
This can be used as a replacement to the official NVIDIA toolkit as it's able to import RTX Remix captures and export assets back in a NVIDIA Omniverse USD compatible format.

## Requirements
- A system running Windows (or use Wine on Linux)
- [Blender 4.0.2](https://download.blender.org/release/Blender4.0/blender-4.0.2-windows-x64.zip) (not verified to work on newer versions)
- [texconv from DirectXTex](https://github.com/microsoft/DirectXTex/releases/latest/download/texconv.exe) (only if using the git method)

## Installation
### Release
1. Download the latest [release](https://github.com/sambow23/blender-remix/releases/latest/download/rtx_remix_importer.zip)
2. Install the addon in Blender:
   - Go to `Edit > Preferences > Add-ons`
   - Click `Install...` and select the `rtx_remix_importer.zip`
   - Enable the `Remix Toolkit for Blender` addon

### Git Repo
1. Clone this repository
2. Place [`texconv.exe`](https://github.com/sambow23/blender-remix/releases/latest/download/texconv.exe) in `rtx_remix_importer/texconv/` (make the `texconv` folder)
2. Install the addon in Blender:
   - Go to `Edit > Preferences > Add-ons`
   - Click `Install...` and select the `rtx_remix_importer` folder
   - Enable the `Remix Toolkit for Blender` addon

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

## Known Issues
- Non-anchor mesh replacements are considered experimental and will have issues
- Albedo textures with an Alpha channel may not import/export correctly
- Some or all .dds textures are purple
   - Happens with invalid .dds texture formats, use `Captures > Fix Broken Textures` to fix it
- The `Aperture Opaque` node group is not hooked up completely, there are some missing features (animation, iridescence, flags, etc)
- Missing other material definitions like `Aperture Translucent` (will be added soon)
- Skinned mesh exporting is not supported yet
- Loading changes from a project's mod.usda can result in undefined behavior. This is being worked on.

## Credits
- Uncle Burrito on the RTX Remix Showcase Discord for the Aperture Opaque node group

If you like what I do, buy me a [coffee](https://ko-fi.com/cattarappa)

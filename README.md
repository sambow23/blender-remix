# Remix Toolkit for Blender
![image](https://github.com/user-attachments/assets/c20812f9-6efb-446a-9c86-e797bb90682e)

A Blender addon that let's you import and export RTX Remix compatible assets.

## Requirements
- A system running Windows (or use Wine on Linux)
- Blender 4.0.2 or higher
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
- Import individual capture USD files or batch import multiple using the checkboxes on the left side of the capture name

### Asset Exports and Management
#### Mod Management
- Under `RTX Remix Project` > `Remix Mod File`, select an existing mod.usda or create a new one

#### Sublayers
- Under `RTX Remix Project` > `Sublayers`, select existing sublayer or create a new one

#### Meshes and Lights
1. Under `RTX Remix Project` > `Remix Anchor Target`. Select an anchor asset using the dropper icon (this would be an object in the game that has a stable hash)
2. Import/create meshes and/or lights and place them anywhere in the capture
3. Select the assets you're going to export, then press `Export selected` under `RTX Remix Project` > `Mesh & Light Exports`

#### Materials
1. Select a mesh you're going to replace the material with, then open the `Shader Editor`
   - By default, RTX Remix materials will use the Aperture Opaque material, but this can be replaced with Principled BSDF if needed.
2. If you want to use Aperture Opaque/Translucent, select `Create Aperture Opaque/Translucent` under `RTX Remix` on the sidebar inside the Shader Editor
3. Connect material inputs/outputs as usual.
4. When you're finished, press `Export selected` under `RTX Remix Project` > `Material Exports`

## Known Issues
- Non-anchor mesh replacements are considered experimental and will have issues
- Albedo textures with an Alpha channel may not import/export correctly
- Some or all .dds textures are purple
   - Happens with invalid .dds texture formats, use `Captures > Fix Broken Textures` to fix it
- The `Aperture Opaque` node group is not hooked up completely, there are some missing features (animation, iridescence, flags, etc)
- Skinned mesh exporting is not supported yet
- Loading changes from a project's mod.usda can result in undefined behavior. This is being worked on.

## Credits
- Uncle Burrito on the RTX Remix Showcase Discord for the Aperture Opaque node group

If you like what I do, buy me a [coffee](https://ko-fi.com/cattarappa)